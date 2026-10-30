"""Versioned rule evaluation with explicit uncertainty and evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

from app.enums.analysis import ValidationSeverity
from app.models.validation_rule import ValidationRule
from app.schemas.analysis_report import ValidationIssue

ENGINE_VERSION = "formal-rules-2.1"

_REASON_MESSAGES = {
    "MASK_TYPE_MISMATCH": "Ukryta wartość dotyczy innego rodzaju danych. Sprawdź właściwe pole.",
    "UNRECOGNIZED_NUMBER": "Nie udało się jednoznacznie odczytać kwoty lub wartości liczbowej.",
    "UNRECOGNIZED_IDENTIFIER": "Nie udało się jednoznacznie odczytać oznaczenia lub numeru.",
    "FIELD_LAYOUT_UNCERTAIN": "Układ dokumentu nie pozwala pewnie przypisać wartości do tego pola.",
    "VARIANT_FIELD_REVIEW": (
        "Sprawdź, czy ten element występuje i jest potrzebny w tym rodzaju dokumentu."
    ),
    "VISUAL_REVIEW_REQUIRED": "Zdjęcie i zabezpieczenia dokumentu wymagają oceny oryginału.",
    "TABLE_CONTENT_UNVERIFIED": (
        "Rozpoznano nagłówki tabeli; sprawdź, czy zawiera także wypełnione pozycje."
    ),
    "SCOPE_NOT_ESTABLISHED": (
        "Odczytano treść pola, ale nie rozpoznano czynności objętej umocowaniem."
    ),
    "EMPTY_FIELD": "Pole jest puste.",
    "INVALID_DATE": "Odczytana data nie jest poprawną datą kalendarzową.",
    "REDACTED_VALUE": "Wartość jest zamaskowana; nie można zweryfikować jej zawartości.",
    "REDACTED_DATE": "Data jest zamaskowana; nie można sprawdzić jej poprawności.",
    "REDACTED_PARTY": "Dane strony są zamaskowane; wymagają weryfikacji w oryginale.",
    "PARTY_FIELD_EMPTY": "Co najmniej jedno pole danych stron jest puste.",
    "PARTY_NOT_FOUND": "Nie odnaleziono danych obu stron w odczytanym dokumencie.",
    "PARTIES_UNCERTAIN": "Nie można jednoznacznie ustalić danych obu stron.",
    "SIGNATURE_REQUIRES_VISUAL_REVIEW": (
        "Sam wynik OCR nie potwierdza obecności ani braku podpisu. Sprawdź podpis na dokumencie."
    ),
    "LOW_OCR_CONFIDENCE": "Odczyt OCR jest zbyt niepewny do rozstrzygnięcia.",
    "CONFLICTING_CANDIDATES": "Odczytano sprzeczne informacje o tym polu.",
    "CONFLICTING_DATES": "Odczytano różne daty; nie można jednoznacznie wybrać daty dokumentu.",
    "UNRECOGNIZED_DATE": "Nie udało się jednoznacznie odczytać daty dokumentu.",
    "INSUFFICIENT_VALUE": (
        "Wykryto pole, ale jego zawartość nie wystarcza do potwierdzenia wymagania."
    ),
    "LAYOUT_UNAVAILABLE": (
        "Brak struktury stron OCR; nie można wiarygodnie potwierdzić braku elementu."
    ),
    "UNREADABLE_PAGE": "Co najmniej jedna strona nie zawiera odczytanych linii tekstu.",
    "INSUFFICIENT_TEXT": "Odczytano zbyt mało tekstu do wiarygodnej oceny braków.",
    "NO_TEXT": "Nie odczytano tekstu dokumentu; walidacja wymaga ponownego OCR.",
    "AZURE_OCR_REQUIRED": "Walidacja formalna wymaga wyniku Azure OCR.",
    "LEGACY_EXTRACTION": "Zapisana ekstrakcja nie zawiera dowodów. Uruchom nową analizę dokumentu.",
    "ATTACHMENT_CONTENT_UNVERIFIED": (
        "Wykaz załączników nie potwierdza, że wymagane pliki zostały dostarczone."
    ),
}


@dataclass
class RuleResult:
    rule_id: str
    rule_name: str
    field_name: str | None
    outcome: str
    severity: str
    reason_code: str
    message: str
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_issue(self) -> ValidationIssue | None:
        if self.outcome in {"pass", "not_applicable"}:
            return None
        severity = self.severity if self.outcome == "fail" else ValidationSeverity.WARNING
        return ValidationIssue(
            rule_name=self.rule_name,
            field_name=self.field_name,
            severity=ValidationSeverity(severity),
            message=self.message,
            details={
                "rule_id": self.rule_id,
                "outcome": self.outcome,
                "reason_code": self.reason_code,
                "evidence": self.evidence,
            },
        )


def rule_snapshot(rule: ValidationRule) -> dict[str, Any]:
    return {
        "name": rule.name,
        "field_name": rule.field_name,
        "rule_type": rule.rule_type,
        "config": rule.rule_config,
        "severity": rule.severity,
        "active": rule.is_active,
        "order": rule.order,
    }


class RuleEngineService:
    def run(
        self, rules: list[ValidationRule], extracted_fields: dict[str, Any]
    ) -> list[ValidationIssue]:
        return [
            issue
            for result in self.evaluate(rules, extracted_fields)
            if (issue := result.to_issue()) is not None
        ]

    def evaluate(self, rules: list[ValidationRule], fields: dict[str, Any]) -> list[RuleResult]:
        return [
            self._evaluate(rule, fields)
            for rule in sorted(rules, key=lambda r: r.order)
            if rule.is_active
        ]

    def _evaluate(self, rule: ValidationRule, fields: dict[str, Any]) -> RuleResult:
        snapshot = json.dumps(rule_snapshot(rule), sort_keys=True, ensure_ascii=False)
        result = RuleResult(
            rule_id=hashlib.sha256(snapshot.encode()).hexdigest()[:16],
            rule_name=rule.name,
            field_name=rule.field_name,
            outcome="error",
            severity=str(rule.severity),
            reason_code="INVALID_RULE_CONFIG",
            message=(
                f"Nie udało się wykonać kontroli „{rule.name}”: nieprawidłowa konfiguracja reguły."
            ),
        )
        if rule.rule_type not in {"required_field", "regex_match", "date_range"}:
            result.reason_code = "UNKNOWN_RULE_TYPE"
            return result
        if not rule.field_name or not isinstance(rule.rule_config, dict):
            return result
        if rule.rule_type == "required_field":
            return self._required(rule, fields, result)
        observation = fields.get("observations", {}).get(rule.field_name, {})
        if observation.get("state") == "masked":
            result.outcome, result.reason_code = "unknown", "MASKED_CONTENT_UNVERIFIABLE"
            result.evidence = observation.get("evidence", [])
            result.message = (
                f"{rule.name}: wartość jest obecna, ale zamaskowana; "
                "nie można sprawdzić jej poprawności."
            )
            return result
        try:
            if rule.rule_type == "regex_match":
                self._regex(rule, fields, result)
            else:
                self._date_range(rule, fields, result)
        except (re.error, TypeError, ValueError):
            return result
        result.message = f"{rule.name}: " + {
            "pass": "wymaganie spełnione.",
            "fail": "wartość nie spełnia wymagania.",
            "unknown": "wartość niedostępna; wymaga weryfikacji.",
        }.get(result.outcome, "błąd wykonania kontroli.")
        return result

    @staticmethod
    def _regex(rule: ValidationRule, fields: dict[str, Any], result: RuleResult) -> None:
        pattern = rule.rule_config.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return
        compiled = re.compile(pattern)
        value = fields.get(rule.field_name or "")
        if value is None:
            result.outcome, result.reason_code = "unknown", "VALUE_UNAVAILABLE"
        else:
            result.outcome = "pass" if compiled.fullmatch(str(value)) else "fail"
            result.reason_code = "PATTERN_MATCH" if result.outcome == "pass" else "PATTERN_MISMATCH"

    @staticmethod
    def _date_range(rule: ValidationRule, fields: dict[str, Any], result: RuleResult) -> None:
        lower, upper = rule.rule_config.get("min"), rule.rule_config.get("max")
        if lower is None and upper is None:
            return
        minimum = date.fromisoformat(lower) if lower else date.min
        maximum = date.fromisoformat(upper) if upper else date.max
        if minimum > maximum:
            return
        value = fields.get(rule.field_name or "")
        try:
            actual = date.fromisoformat(str(value))
        except ValueError:
            result.outcome = "unknown" if value is None else "fail"
            result.reason_code = "VALUE_UNAVAILABLE" if value is None else "INVALID_DATE"
        else:
            result.outcome = "pass" if minimum <= actual <= maximum else "fail"
            result.reason_code = (
                "DATE_IN_RANGE" if result.outcome == "pass" else "DATE_OUT_OF_RANGE"
            )

    def _required(
        self, rule: ValidationRule, fields: dict[str, Any], result: RuleResult
    ) -> RuleResult:
        observation = fields.get("observations", {}).get(rule.field_name or "")
        if not isinstance(observation, dict):
            if rule.field_name and rule.field_name.startswith("has_"):
                result.outcome, result.reason_code = "unknown", "LEGACY_EXTRACTION"
                result.message = _REASON_MESSAGES[result.reason_code]
                return result
            value = fields.get(rule.field_name or "")
            present = value is not None and value is not False and value not in ("", [])
            observation = {"state": "present" if present else "missing", "reason_code": "NOT_FOUND"}
        state = str(observation.get("state", ""))
        result.outcome = {
            "present": "pass",
            "empty": "fail",
            "missing": "fail",
            "invalid": "fail",
            "masked": "pass",
            "unknown": "unknown",
            "not_applicable": "not_applicable",
        }.get(state, "error")
        result.reason_code = observation.get("reason_code", "INVALID_OBSERVATION")
        result.evidence = observation.get("evidence", [])
        if state == "masked":
            result.reason_code = "REDACTED_PRESENCE_CONFIRMED"
            result.message = (
                f"{rule.name}: obecność potwierdzona znacznikiem anonimizacji. "
                "Zawartość ukrytej wartości nie była sprawdzana."
            )
        elif result.outcome == "pass":
            result.message = f"{rule.name}: potwierdzono na podstawie wyniku Azure OCR."
        elif result.outcome == "not_applicable":
            result.message = f"{rule.name}: kontrola nie dotyczy tego dokumentu."
        elif result.reason_code in _REASON_MESSAGES:
            result.message = f"{rule.name}: {_REASON_MESSAGES[result.reason_code]}"
        elif result.outcome == "fail":
            result.message = (
                rule.rule_config.get("message")
                or f"Nie odnaleziono wymaganego elementu: {rule.name}."
            )
        else:
            result.message = f"{rule.name}: nie można rozstrzygnąć wyniku kontroli."
        return result


def aggregate_results(results: list[RuleResult], profile_available: bool) -> dict[str, Any]:
    applicable = [r for r in results if r.outcome != "not_applicable"]
    required = [r for r in applicable if r.severity == ValidationSeverity.ERROR]
    failed = any(r.outcome == "fail" for r in required)
    uncertain = any(r.outcome in {"unknown", "error"} for r in required)
    if not profile_available:
        status = "unsupported"
    elif failed:
        status = "incomplete"
    elif uncertain or not required:
        status = "indeterminate"
    else:
        status = "complete"
    complete = {"complete": True, "incomplete": False}.get(status)
    # Score is withheld whenever required controls could not be completed.
    score = None
    if profile_available and required and not uncertain:
        score = round(sum(r.outcome == "pass" for r in required) / len(required), 4)
    coverage = (
        round(sum(r.outcome in {"pass", "fail"} for r in applicable) / len(applicable), 4)
        if applicable
        else None
    )
    return {
        "validation_status": status,
        "is_complete": complete,
        "completeness_score": score,
        "coverage": coverage,
        "total_rules": len(results),
        "counts": {
            outcome: sum(r.outcome == outcome for r in results)
            for outcome in ("pass", "fail", "unknown", "not_applicable", "error")
        },
        "rule_results": [asdict(result) for result in results],
    }
