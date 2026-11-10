"""One report contract shared by Celery and the application service."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.config import settings
from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.models.validation_rule import ValidationRule
from app.schemas.analysis_report import ValidationIssue
from app.services.formal_context import normalize
from app.services.validation import (
    ENGINE_VERSION,
    RuleEngineService,
    RuleResult,
    aggregate_results,
    rule_snapshot,
)


def effective_document_type(
    requested: DocumentType, detected: DocumentType, confidence: float
) -> DocumentType:
    if requested != DocumentType.UNKNOWN:
        return requested
    return detected if confidence >= settings.CLASSIFIER_MIN_CONFIDENCE else DocumentType.UNKNOWN


def coerce_document_type(value: object) -> DocumentType:
    try:
        return DocumentType(value) if isinstance(value, str) else DocumentType.UNKNOWN
    except ValueError:
        return DocumentType.UNKNOWN


def resolve_document_type(
    requested_value: object, detected_value: object, confidence: float
) -> tuple[DocumentType, dict[str, Any]]:
    requested = coerce_document_type(requested_value)
    detected = coerce_document_type(detected_value)
    return effective_document_type(requested, detected, confidence), {
        "requested_document_type": requested.value,
        "detected_document_type": detected.value,
        "classification_confidence": confidence,
        "type_source": "user" if requested != DocumentType.UNKNOWN else "automatic",
    }


class FormalValidationService:
    def build_report(
        self,
        rules: list[ValidationRule],
        fields: dict[str, Any],
        profile_name: str | None,
        document_type: DocumentType,
    ) -> tuple[list[ValidationIssue], dict[str, Any]]:
        results = RuleEngineService().evaluate(rules, fields)
        available = bool(profile_name and any(rule.is_active for rule in rules))
        if not fields.get("has_text"):
            results.insert(
                0,
                RuleResult(
                    "ocr.readable_text",
                    "Tekst dokumentu",
                    "has_text",
                    "unknown",
                    ValidationSeverity.ERROR,
                    "NO_TEXT",
                    "Nie odczytano tekstu; nie można ocenić kompletności dokumentu.",
                ),
            )
        elif "UNREADABLE_PAGE" in fields.get("ocr_limitations", []):
            results.insert(
                0,
                RuleResult(
                    "ocr.readable_pages",
                    "Czytelność stron dokumentu",
                    None,
                    "unknown",
                    ValidationSeverity.ERROR,
                    "UNREADABLE_PAGE",
                    "Nie odczytano tekstu z co najmniej jednej strony. "
                    "Sprawdź, czy jest pusta, czy wymaga wyraźniejszej kopii.",
                ),
            )
        summary = aggregate_results(results, available)
        issues = [issue for result in results if (issue := result.to_issue()) is not None]
        additional = _empty_field_issues(fields, results)
        issues.extend(additional)
        if not available:
            issues.append(
                ValidationIssue(
                    rule_name="Formal validation profile",
                    field_name="document_type",
                    severity=ValidationSeverity.WARNING,
                    message=(
                        "Brak aktywnego profilu walidacji dla tego typu dokumentu. "
                        "Kompletność nie została oceniona."
                    ),
                    details={
                        "reason_code": "UNSUPPORTED_PROFILE",
                        "outcome": "unknown",
                        "document_type": document_type.value,
                    },
                )
            )
        snapshot = [
            rule_snapshot(rule) for rule in sorted(rules, key=lambda rule: (rule.order, rule.name))
        ]
        summary.update(
            {
                "schema_version": 2,
                "engine_version": ENGINE_VERSION,
                "extraction_version": fields.get("extraction_version"),
                "input_fingerprint": fields.get("input_fingerprint"),
                "profile_version": hashlib.sha256(
                    json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest(),
                "profile_snapshot": snapshot,
                "profile_name": profile_name,
                "validation_profile_available": available,
                "effective_document_type": document_type.value,
                "issues": len(issues),
                "additional_review_count": len(additional),
                "ocr_limitations": fields.get("ocr_limitations", []),
                "checked_fields": {
                    key: value for key, value in fields.items() if key.startswith("has_")
                },
                "detected_sections": fields.get("detected_sections", []),
            }
        )
        return issues, summary


def _empty_field_issues(fields: dict[str, Any], results: list[RuleResult]) -> list[ValidationIssue]:
    """Advisory findings do not change required checks, scores or coverage."""
    issues = []
    for finding in fields.get("possible_empty_fields", []):
        evidence = finding["evidence"]
        checked_evidence = {
            (entry.get("page"), entry.get("text"), tuple(entry.get("polygon") or []))
            for result in results
            if not finding.get("recognized_label")
            or normalize(result.rule_name) == normalize(finding["label"])
            for entry in result.evidence
        }
        # A field already handled by a profile gets one result, not a second warning.
        if evidence and all(
            (entry.get("page"), entry.get("text"), tuple(entry.get("polygon") or []))
            in checked_evidence
            for entry in evidence
        ):
            continue
        page = finding.get("page")
        where = f" (strona {page})" if page else ""
        issues.append(
            ValidationIssue(
                rule_name=finding["label"],
                field_name="possible_empty_field",
                severity=ValidationSeverity.WARNING,
                message=f"Możliwe niewypełnione pole „{finding['label']}”{where}. "
                "Sprawdź, czy dotyczy tego dokumentu i wymaga uzupełnienia.",
                details={
                    "rule_id": "blank." + finding["id"],
                    "outcome": "unknown",
                    "reason_code": "POSSIBLE_EMPTY_FIELD",
                    "advisory": True,
                    "evidence": evidence,
                },
            )
        )
    return issues
