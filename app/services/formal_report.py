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
                    "Nie odczytano tekstu z Azure OCR; nie można ocenić kompletności dokumentu.",
                ),
            )
        summary = aggregate_results(results, available)
        issues = [issue for result in results if (issue := result.to_issue()) is not None]
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
                "document_type": document_type.value,
                "effective_document_type": document_type.value,
                "issues": len(issues),
                "ocr_limitations": fields.get("ocr_limitations", []),
                "checked_fields": {
                    key: value for key, value in fields.items() if key.startswith("has_")
                },
                "detected_sections": fields.get("detected_sections", []),
            }
        )
        return issues, summary
