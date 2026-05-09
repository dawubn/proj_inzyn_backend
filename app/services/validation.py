"""
Validation / rule engine service.

The rule engine evaluates a list of ValidationRule objects against
extracted document fields. Rules are evaluated in order.
Add concrete rule evaluators in the _evaluate_rule method.
"""

import re
from collections.abc import Callable
from typing import Any

import structlog

from app.models.validation_rule import ValidationRule
from app.schemas.analysis_report import ValidationIssue

logger = structlog.get_logger(__name__)


class RuleEngineService:
    """Evaluates validation rules against extracted document fields.

    This service is intentionally decoupled from the API layer and OCR adapter.
    It receives already-extracted fields and a list of rules, returns issues.
    """

    def run(
        self,
        rules: list[ValidationRule],
        extracted_fields: dict[str, Any],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []

        for rule in sorted(rules, key=lambda r: r.order):
            if not rule.is_active:
                continue
            issue = self._evaluate_rule(rule, extracted_fields)
            if issue:
                issues.append(issue)

        return issues

    def _evaluate_rule(
        self, rule: ValidationRule, fields: dict[str, Any]
    ) -> ValidationIssue | None:
        """Dispatch to concrete rule evaluators based on rule_type."""
        evaluator = self._get_evaluator(rule.rule_type)
        if evaluator is None:
            logger.warning("Unknown rule type", rule_type=rule.rule_type, rule_id=str(rule.id))
            return None
        return evaluator(rule, fields)

    def _get_evaluator(
        self, rule_type: str
    ) -> Callable[[ValidationRule, dict[str, Any]], ValidationIssue | None] | None:
        mapping: dict[str, Callable[[ValidationRule, dict[str, Any]], ValidationIssue | None]] = {
            "required_field": self._check_required_field,
            "regex_match": self._check_regex_match,
            "date_range": self._check_date_range,
        }
        return mapping.get(rule_type)

    def _check_required_field(
        self, rule: ValidationRule, fields: dict[str, Any]
    ) -> ValidationIssue | None:
        field = rule.field_name
        if not field or fields.get(field):
            return None
        return ValidationIssue(
            rule_name=rule.name,
            field_name=field,
            severity=rule.severity,
            message=f"Required field '{field}' is missing or empty",
        )

    def _check_regex_match(
        self, rule: ValidationRule, fields: dict[str, Any]
    ) -> ValidationIssue | None:
        field = rule.field_name
        pattern = rule.rule_config.get("pattern")
        if not field or not pattern:
            return None
        value = str(fields.get(field, ""))
        if not re.fullmatch(pattern, value):
            return ValidationIssue(
                rule_name=rule.name,
                field_name=field,
                severity=rule.severity,
                message=f"Field '{field}' does not match required pattern",
                details={"pattern": pattern, "value": value},
            )
        return None

    def _check_date_range(
        self, _rule: ValidationRule, _fields: dict[str, Any]
    ) -> ValidationIssue | None:
        return None
