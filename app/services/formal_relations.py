"""Conservative, role-labelled date extraction for cross-field validation."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.models.validation_rule import ValidationRule
from app.services.formal_context import MIN_WORD_CONFIDENCE, DocumentContext, normalize

# These labels identify roles; arbitrary dates in prose are deliberately not compared.
_DATE_ROLES = {
    DocumentType.CONTRACT: ("rozpoczecia", "zakonczenia", "Okres obowiązywania umowy"),
    DocumentType.PASSPORT: ("wydania", "waznosci", "Okres ważności paszportu"),
    DocumentType.ID_CARD: ("wydania", "waznosci", "Okres ważności dowodu osobistego"),
}
_DATE = re.compile(r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4})(?!\d)")


def extract_relation_dates(context: DocumentContext, kind: DocumentType) -> dict[str, Any]:
    roles = _DATE_ROLES.get(kind)
    if not roles:
        return {}
    result: dict[str, Any] = {}
    for key, role in zip(("period_start", "period_end"), roles[:2], strict=True):
        candidates: list[dict[str, Any]] = []
        for line in context.lines:
            match = re.match(rf"^\s*data {role}(?: umowy)?\s*:\s*(.*)$", normalize(line.text))
            if not match:
                continue
            value = match[1]
            dates = _DATE.findall(value)
            state, parsed = "unknown", None
            if "utajn" in value:
                state = "masked"
            elif (
                not context.limitations
                and line.confidence is not None
                and line.confidence >= MIN_WORD_CONFIDENCE
                and len(dates) == 1
                and re.fullmatch(re.escape(dates[0]) + r"\s*(?:r\.?)?", value)
            ):
                try:
                    token = dates[0]
                    if not re.match(r"^\d{4}-", token):
                        day, month, year = re.split(r"[./-]", token)
                        token = f"{year}-{int(month):02d}-{int(day):02d}"
                    parsed, state = date.fromisoformat(token).isoformat(), "present"
                except ValueError:
                    state = "invalid"
            candidates.append({"state": state, "value": parsed, "evidence": [line.evidence()]})
        if candidates:
            first = candidates[0]
            if any(
                (c["state"], c["value"]) != (first["state"], first["value"]) for c in candidates
            ):
                first = {"state": "unknown", "value": None}
            result[key] = {**first, "evidence": [e for c in candidates for e in c["evidence"]]}
    return result


def relation_rules(kind: DocumentType, fields: dict[str, Any]) -> list[ValidationRule]:
    roles = _DATE_ROLES.get(kind)
    observations = fields.get("observations") or {}
    if not roles or not all(key in observations for key in ("period_start", "period_end")):
        return []
    return [
        ValidationRule(
            name=roles[2],
            field_name="period_end",
            rule_type="compare_fields",
            rule_config={
                "other_field": "period_start",
                "operator": "gte",
                "value_type": "date",
                "message": "Data końcowa jest wcześniejsza niż data początkowa. Sprawdź obie daty.",
            },
            severity=ValidationSeverity.WARNING,
            is_active=True,
            order=1000,
        )
    ]
