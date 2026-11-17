"""Versioned rule evaluation with explicit uncertainty and evidence."""

from __future__ import annotations

import hashlib
import json
import operator
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.enums.analysis import ValidationSeverity
from app.models.validation_rule import ValidationRule
from app.schemas.analysis_report import ValidationIssue

ENGINE_VERSION = "formal-rules-2.3"

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
        active = sorted((r for r in rules if r.is_active), key=lambda r: r.order)
        by_name = {r.name: r for r in active}
        results: dict[int, RuleResult] = {}
        visiting: set[int] = set()

        def visit(rule: ValidationRule) -> RuleResult:
            if id(rule) in results:
                return results[id(rule)]
            if id(rule) in visiting:
                return self._blocked(
                    rule, "error", "INVALID_DEPENDENCY", "Wykryto cykl zależności reguł."
                )
            visiting.add(id(rule))
            config = rule.rule_config
            dependencies = config.get("depends_on", []) if isinstance(config, dict) else None
            if (
                not isinstance(dependencies, list)
                or any(not isinstance(name, str) or name not in by_name for name in dependencies)
                or any(sum(r.name == name for r in active) != 1 for name in dependencies)
            ):
                result = self._blocked(
                    rule,
                    "error",
                    "INVALID_DEPENDENCY",
                    "Nieprawidłowa konfiguracja zależności reguły.",
                )
            else:
                prerequisites = [visit(by_name[name]) for name in dependencies]
                if any(item.outcome == "error" for item in prerequisites):
                    result = self._blocked(
                        rule,
                        "error",
                        "INVALID_DEPENDENCY",
                        "Nie można wykonać zależnej reguły z powodu błędu konfiguracji.",
                    )
                elif any(item.outcome != "pass" for item in prerequisites):
                    result = self._blocked(
                        rule,
                        "unknown",
                        "DEPENDENCY_UNRESOLVED",
                        "Najpierw należy potwierdzić dane wymagane przez tę regułę.",
                    )
                else:
                    result = self._condition(rule, fields) or self._evaluate(rule, fields)
            visiting.remove(id(rule))
            results[id(rule)] = result
            return result

        return [visit(rule) for rule in active]

    @staticmethod
    def _blocked(rule: ValidationRule, outcome: str, reason: str, message: str) -> RuleResult:
        snapshot = json.dumps(rule_snapshot(rule), sort_keys=True, ensure_ascii=False)
        return RuleResult(
            hashlib.sha256(snapshot.encode()).hexdigest()[:16],
            rule.name,
            rule.field_name,
            outcome,
            str(rule.severity),
            reason,
            message,
        )

    def _condition(self, rule: ValidationRule, fields: dict[str, Any]) -> RuleResult | None:
        condition = rule.rule_config.get("when")
        if condition is None:
            return None
        if (
            not isinstance(condition, dict)
            or not isinstance(condition.get("field"), str)
            or set(condition) != {"field", "equals"}
            or not isinstance(condition["equals"], str | bool | int | float)
        ):
            return self._blocked(
                rule, "error", "INVALID_RULE_CONFIG", "Nieprawidłowy warunek wykonania reguły."
            )
        observations = fields.get("observations") or {}
        if not isinstance(observations, dict):
            return self._blocked(
                rule, "error", "INVALID_OBSERVATION", "Nieprawidłowy zapis danych kontroli."
            )
        observation = observations.get(condition["field"], {})
        value = fields.get(condition["field"])
        if (
            not isinstance(observation, dict)
            or observation.get("state") in {"unknown", "masked", "invalid"}
            or value is None
        ):
            return self._blocked(
                rule,
                "unknown",
                "CONDITION_UNRESOLVED",
                "Nie można ustalić, czy ta kontrola dotyczy dokumentu.",
            )
        if value != condition["equals"]:
            return self._blocked(
                rule,
                "not_applicable",
                "CONDITION_NOT_MET",
                "Warunek tej kontroli nie dotyczy dokumentu.",
            )
        return None

    def _evaluate(  # noqa: PLR0911 - explicit outcomes keep uncertain data out of value checks
        self, rule: ValidationRule, fields: dict[str, Any]
    ) -> RuleResult:
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
        if rule.rule_type not in {"required_field", "regex_match", "date_range", "compare_fields"}:
            result.reason_code = "UNKNOWN_RULE_TYPE"
            return result
        if not rule.field_name or not isinstance(rule.rule_config, dict):
            return result
        if rule.rule_type == "compare_fields":
            return self._compare(rule, fields, result)
        observations = fields.get("observations") or {}
        observation = observations.get(rule.field_name) if isinstance(observations, dict) else None
        if not isinstance(observations, dict) or (
            observation is not None and not isinstance(observation, dict)
        ):
            result.reason_code = "INVALID_OBSERVATION"
            result.message = "Nie udało się odczytać zapisanych wyników. Uruchom analizę ponownie."
            return result
        if rule.rule_type == "required_field":
            return self._required(rule, fields, result, observation)
        observation = observation or {}
        result.evidence = observation.get("evidence", [])
        if observation.get("state") in {"unknown", "not_applicable"}:
            result.outcome = observation["state"]
            result.reason_code = observation.get("reason_code", "VALUE_UNAVAILABLE")
            result.message = _REASON_MESSAGES.get(
                result.reason_code, "Nie można rozstrzygnąć wyniku tej kontroli. Sprawdź dokument."
            )
            return result
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
    def _compare(  # noqa: PLR0911, PLR0912 - explicit uncertainty guards for both operands
        rule: ValidationRule, fields: dict[str, Any], result: RuleResult
    ) -> RuleResult:
        config = rule.rule_config
        operations = {
            "eq": operator.eq,
            "ne": operator.ne,
            "lt": operator.lt,
            "lte": operator.le,
            "gt": operator.gt,
            "gte": operator.ge,
        }
        other, op, kind = (
            config.get("other_field"),
            config.get("operator"),
            config.get("value_type"),
        )
        if (
            not isinstance(other, str)
            or not other
            or not isinstance(op, str)
            or op not in operations
            or not isinstance(kind, str)
            or kind not in {"date", "number", "text"}
        ):
            return result
        if kind == "text" and op not in {"eq", "ne"}:
            return result
        observations = fields.get("observations") or {}
        if not isinstance(observations, dict):
            return result
        values: list[Any] = []
        for key in (rule.field_name or "", other):
            observation = observations.get(key, {})
            if not isinstance(observation, dict):
                return result
            result.evidence.extend(observation.get("evidence", []))
            value = fields.get(key)
            # Both operands must be confirmed, readable observations.
            if observation.get("state") != "present" or value is None or isinstance(value, bool):
                values.append(None)
            else:
                values.append(value)
        if any(value is None for value in values):
            result.outcome, result.reason_code = "unknown", "COMPARISON_UNAVAILABLE"
            result.message = (
                "Nie można porównać pól: dane są niepełne, zamaskowane lub odczyt jest niepewny."
            )
            return result
        left: Any
        right: Any
        try:
            if kind == "date":
                left, right = (date.fromisoformat(str(value)) for value in values)
            elif kind == "number":
                left, right = (Decimal(str(value)) for value in values)
                if not left.is_finite() or not right.is_finite():
                    raise ValueError("Non-finite value")
            else:
                left, right = (" ".join(str(value).casefold().split()) for value in values)
            passed = operations[op](left, right)
        except (ValueError, TypeError, InvalidOperation):
            result.outcome, result.reason_code = "unknown", "COMPARISON_UNAVAILABLE"
            result.message = "Nie udało się odczytać wartości potrzebnych do porównania pól."
            return result
        result.outcome = "pass" if passed else "fail"
        result.reason_code = "FIELDS_CONSISTENT" if passed else "FIELDS_INCONSISTENT"
        result.message = (
            "Porównane dane są zgodne z regułą."
            if passed
            else config.get("message") or "Dane w porównanych polach są niespójne."
        )
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
        self,
        rule: ValidationRule,
        fields: dict[str, Any],
        result: RuleResult,
        observation: dict[str, Any] | None,
    ) -> RuleResult:
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
            result.message = f"{rule.name}: potwierdzono obecność wymaganego elementu."
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
