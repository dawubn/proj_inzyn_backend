"""Compare report results with validation recomputed from analysis fields.

Without an external reference, the rule engine is run locally against
analysis.extracted_fields using the report's recorded profile snapshot.

This verifies result serialization for that recorded configuration.
It does not independently verify profile selection or detection accuracy.
PDF checks verify readability and the heading, not every exported result.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .models import AnalysisResult, Outcome, ReportCheckResult, ReportDifference, ReportMetrics

VALID_OUTCOMES = frozenset(item.value for item in Outcome)
MISSING = {"missing": True}

RULE_COMPARISON_KEYS = (
    "field_name",
    "rule_name",
    "outcome",
    "severity",
    "reason_code",
    "message",
    "evidence",
)

SUMMARY_COMPARISON_KEYS = (
    "counts",
    "total_rules",
    "validation_status",
    "is_complete",
    "completeness_score",
    "coverage",
)


def _equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right

    if isinstance(left, int | float) and isinstance(right, int | float):
        return (
            math.isfinite(left)
            and math.isfinite(right)
            and math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
        )

    if type(left) is not type(right):
        return False

    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _equal(value, right[key]) for key, value in left.items()
        )

    if isinstance(left, list):
        return len(left) == len(right) and all(
            _equal(a, b) for a, b in zip(left, right, strict=True)
        )

    return bool(left == right)


def _recompute_summary(
    analysis: AnalysisResult,
    report_summary: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-evaluate recorded rules without reading reported outcomes."""

    from app.models.validation_rule import ValidationRule
    from app.services.validation import RuleEngineService, aggregate_results

    payload = analysis.analysis_payload

    if not isinstance(payload, dict):
        raise TypeError("Analysis payload is unavailable.")

    extracted = payload.get("extracted_fields")

    if not isinstance(extracted, dict):
        raise TypeError("Analysis extracted_fields are unavailable.")

    snapshot = report_summary.get("profile_snapshot")

    if not isinstance(snapshot, list) or not snapshot:
        raise ValueError("Recorded profile snapshot is unavailable.")

    rules: list[ValidationRule] = []

    for index, item in enumerate(snapshot):
        if not isinstance(item, dict):
            raise TypeError(f"Invalid profile rule at index {index}.")

        if (
            not isinstance(item.get("name"), str)
            or not isinstance(item.get("rule_type"), str)
            or not isinstance(item.get("config"), dict)
            or type(item.get("active")) is not bool
            or type(item.get("order")) is not int
        ):
            raise ValueError(f"Malformed profile rule at index {index}.")

        rules.append(
            ValidationRule(
                name=item["name"],
                field_name=item.get("field_name"),
                rule_type=item["rule_type"],
                rule_config=item["config"],
                severity=item.get("severity", "error"),
                is_active=item["active"],
                order=item["order"],
            )
        )

    evaluated = RuleEngineService().evaluate(rules, extracted)

    # A non-empty recorded profile supplies the evaluated rules.
    return aggregate_results(evaluated, True)


def _index_rules(
    summary: Mapping[str, Any],
    source: str,
    differences: list[ReportDifference],
) -> dict[str, dict[str, Any]]:
    items = summary.get("rule_results")

    if not isinstance(items, list) or not items:
        differences.append(
            ReportDifference(
                check=f"{source}.rule_results",
                message="Expected a non-empty list of rule results.",
                actual=items,
            )
        )
        return {}

    indexed: dict[str, dict[str, Any]] = {}

    for index, item in enumerate(items):
        if not isinstance(item, dict):
            differences.append(
                ReportDifference(
                    check=f"{source}.rule_object",
                    message=f"Invalid rule object at index {index}.",
                )
            )
            continue

        rule_id = item.get("rule_id")
        outcome = item.get("outcome")

        if not isinstance(rule_id, str) or not rule_id.strip():
            differences.append(
                ReportDifference(
                    check=f"{source}.rule_id",
                    message=f"Invalid rule ID at index {index}.",
                    actual=rule_id,
                )
            )
            continue

        if rule_id in indexed:
            differences.append(
                ReportDifference(
                    check=f"{source}.duplicate_rule",
                    message="Duplicate rule ID.",
                    actual=rule_id,
                )
            )
            continue

        if not isinstance(outcome, str) or outcome not in VALID_OUTCOMES:
            differences.append(
                ReportDifference(
                    check=f"{source}.outcome",
                    message=f"Invalid outcome for {rule_id}.",
                    actual=outcome,
                )
            )

        indexed[rule_id] = item

    return indexed


def _counter_entries(
    counter: Counter[tuple[str, Any]],
) -> list[dict[str, Any]]:
    return [
        {"rule_id": key[0], "outcome": key[1], "count": count}
        for key, count in sorted(counter.items())
    ]


class ReportValidator:
    """Collect differences between one report and its analysis reference."""

    def __init__(
        self,
        analysis: AnalysisResult,
        report: Mapping[str, Any],
    ) -> None:
        self.analysis = analysis
        self.report = report
        self.differences: list[ReportDifference] = []

    def result(self, *, checked: bool = True) -> ReportCheckResult:
        return ReportCheckResult(
            case_id=self.analysis.case_id,
            available=True,
            checked=checked,
            differences=tuple(self.differences),
        )

    def compare(
        self,
        check: str,
        expected: Any,
        actual: Any,
        field_key: str | None = None,
    ) -> None:
        if not _equal(expected, actual):
            self.differences.append(
                ReportDifference(
                    check=check,
                    message="Report value does not match analysis results.",
                    field_key=field_key,
                    expected=expected,
                    actual=actual,
                )
            )

    def check_metadata(self) -> None:
        payload = self.analysis.analysis_payload or {}

        self.compare("analysis_status", "completed", payload.get("status", MISSING))
        self.compare("report_status", "completed", self.report.get("status", MISSING))
        self.compare("processed", True, self.analysis.processed)

        if self.analysis.analysis_id is None:
            self.differences.append(
                ReportDifference(
                    check="analysis_id",
                    message="Analysis ID is unavailable.",
                )
            )
        else:
            self.compare(
                "analysis_payload.id",
                self.analysis.analysis_id,
                payload.get("id", MISSING),
            )
            self.compare(
                "report.analysis_id",
                self.analysis.analysis_id,
                self.report.get("analysis_id", MISSING),
            )

        if self.analysis.document_id is not None:
            self.compare(
                "analysis_payload.document_id",
                self.analysis.document_id,
                payload.get("document_id", MISSING),
            )

    def check_rule_presence(
        self,
        expected_rules: Mapping[str, dict[str, Any]],
        actual_rules: Mapping[str, dict[str, Any]],
    ) -> None:
        for rule_id in sorted(expected_rules.keys() - actual_rules.keys()):
            self.differences.append(
                ReportDifference(
                    check="missing_rule",
                    message=f"Missing report result: {rule_id}.",
                    field_key=expected_rules[rule_id].get("field_name"),
                    expected=expected_rules[rule_id],
                )
            )

        for rule_id in sorted(actual_rules.keys() - expected_rules.keys()):
            self.differences.append(
                ReportDifference(
                    check="unexpected_rule",
                    message=f"Unexpected report result: {rule_id}.",
                    actual=actual_rules[rule_id],
                )
            )

    def check_rule_values(
        self,
        expected_rules: Mapping[str, dict[str, Any]],
        actual_rules: Mapping[str, dict[str, Any]],
    ) -> None:
        for rule_id in sorted(expected_rules.keys() & actual_rules.keys()):
            expected = expected_rules[rule_id]
            actual = actual_rules[rule_id]

            for key in RULE_COMPARISON_KEYS:
                if key in expected:
                    self.compare(
                        f"rule.{rule_id}.{key}",
                        expected[key],
                        actual.get(key, MISSING),
                        expected.get("field_name"),
                    )

    def check_summary(
        self,
        reference_summary: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> None:
        for key in SUMMARY_COMPARISON_KEYS:
            if key in reference_summary:
                self.compare(
                    f"summary.{key}",
                    reference_summary[key],
                    summary.get(key, MISSING),
                )

        for key in ("is_complete", "completeness_score"):
            self.compare(
                f"report.{key}",
                reference_summary.get(key, MISSING),
                self.report.get(key, MISSING),
            )

    def issue_reference(
        self,
        issue: Any,
        list_name: str,
    ) -> tuple[str, str] | None:
        if not isinstance(issue, dict):
            self.differences.append(
                ReportDifference(
                    check=f"{list_name}.issue",
                    message="Malformed report issue.",
                )
            )
            return None

        details = issue.get("details")

        if not isinstance(details, dict):
            self.differences.append(
                ReportDifference(
                    check=f"{list_name}.details",
                    message="Malformed issue details.",
                )
            )
            return None

        rule_id = details.get("rule_id")

        if rule_id is None:
            return None

        outcome = details.get("outcome")

        if not isinstance(rule_id, str) or not isinstance(outcome, str):
            self.differences.append(
                ReportDifference(
                    check="issue_rule_reference",
                    message="Invalid rule reference.",
                    actual=details,
                )
            )
            return None

        return rule_id, outcome

    def check_issue_values(
        self,
        issue: Mapping[str, Any],
        rule_id: str,
        expected_rule: Mapping[str, Any],
    ) -> None:
        self.compare(
            f"issue.{rule_id}.field_name",
            expected_rule.get("field_name"),
            issue.get("field_name", MISSING),
        )

        expected_severity = (
            expected_rule.get("severity") if expected_rule.get("outcome") == "fail" else "warning"
        )
        self.compare(
            f"issue.{rule_id}.severity",
            expected_severity,
            issue.get("severity", MISSING),
        )

    def collect_issues(
        self,
        expected_rules: Mapping[str, dict[str, Any]],
    ) -> Counter[tuple[str, Any]]:
        actual_issues: Counter[tuple[str, Any]] = Counter()

        for list_name, count_key in (
            ("errors", "error_count"),
            ("warnings", "warning_count"),
            ("infos", None),
        ):
            issues = self.report.get(list_name)

            if not isinstance(issues, list):
                self.differences.append(
                    ReportDifference(
                        check=list_name,
                        message="Expected a list of issues.",
                        actual=issues,
                    )
                )
                continue

            if count_key:
                self.compare(
                    count_key,
                    len(issues),
                    self.report.get(count_key, MISSING),
                )

            for issue in issues:
                reference = self.issue_reference(issue, list_name)

                if reference is None:
                    continue

                actual_issues[reference] += 1
                rule_id, _ = reference

                if rule_id in expected_rules:
                    self.check_issue_values(
                        issue,
                        rule_id,
                        expected_rules[rule_id],
                    )

        return actual_issues

    def check_issues(
        self,
        expected_rules: Mapping[str, dict[str, Any]],
    ) -> None:
        expected_issues: Counter[tuple[str, Any]] = Counter(
            (rule_id, item.get("outcome"))
            for rule_id, item in expected_rules.items()
            if item.get("outcome") in {"fail", "unknown", "error"}
        )
        actual_issues = self.collect_issues(expected_rules)

        if expected_issues != actual_issues:
            self.differences.append(
                ReportDifference(
                    check="issues_match_rule_results",
                    message="Rule-linked issues differ from analysis results.",
                    expected=_counter_entries(expected_issues),
                    actual=_counter_entries(actual_issues),
                )
            )

    def inspect_pdf(self) -> None:
        import pymupdf

        with pymupdf.open(str(self.analysis.report_pdf_path)) as pdf:
            if pdf.page_count < 1:
                raise ValueError("PDF has no pages.")

            text = " ".join(page.get_text() for page in pdf)
            normalized = " ".join(text.casefold().split())

            if "raport z analizy dokumentu" not in normalized:
                self.differences.append(
                    ReportDifference(
                        check="pdf_heading",
                        message="Expected report heading was not found.",
                    )
                )

    def check_pdf(self) -> None:
        if not self.analysis.pdf_downloaded or self.analysis.report_pdf_path is None:
            self.differences.append(
                ReportDifference(
                    check="pdf_available",
                    message="PDF export is unavailable.",
                    actual=self.analysis.export_error,
                )
            )
            return

        try:
            self.inspect_pdf()
        except Exception as exc:
            self.differences.append(
                ReportDifference(
                    check="pdf_readable",
                    message="Cannot inspect exported PDF.",
                    actual=f"{type(exc).__name__}: {exc}",
                )
            )

    def check_results(
        self,
        reference_summary: Mapping[str, Any],
        summary: Mapping[str, Any],
    ) -> None:
        expected_rules = _index_rules(
            reference_summary,
            "reference",
            self.differences,
        )
        actual_rules = _index_rules(
            summary,
            "report",
            self.differences,
        )

        self.check_rule_presence(expected_rules, actual_rules)
        self.check_rule_values(expected_rules, actual_rules)
        self.check_summary(reference_summary, summary)
        self.check_issues(expected_rules)
        self.check_pdf()


def validate_report(
    analysis: AnalysisResult,
    *,
    reference_summary: Mapping[str, Any] | None = None,
) -> ReportCheckResult:
    report = analysis.report_payload

    if not isinstance(report, dict):
        return ReportCheckResult(
            case_id=analysis.case_id,
            available=False,
            checked=False,
            differences=(
                ReportDifference(
                    check="report_available",
                    message="JSON report is unavailable.",
                    actual=analysis.error,
                ),
            ),
        )

    validator = ReportValidator(analysis, report)
    validator.check_metadata()
    summary = report.get("summary")

    if not isinstance(summary, dict):
        validator.differences.append(
            ReportDifference(
                check="summary",
                message="Report summary is missing or malformed.",
                actual=summary,
            )
        )
        return validator.result()

    if reference_summary is report or reference_summary is summary:
        raise ValueError("Report cannot be used as its own reference.")

    if reference_summary is None:
        try:
            reference_summary = _recompute_summary(analysis, summary)
        except Exception as exc:
            validator.differences.append(
                ReportDifference(
                    check="analysis_reference",
                    message=(
                        "Could not recompute formal results from analysis "
                        "fields and the recorded profile configuration."
                    ),
                    actual=f"{type(exc).__name__}: {exc}",
                )
            )
            return validator.result(checked=False)

    validator.check_results(reference_summary, summary)
    return validator.result()


def aggregate_report_metrics(
    results: Iterable[ReportCheckResult],
    *,
    expected_count: int,
) -> ReportMetrics:
    items = list(results)
    identifiers = [item.case_id for item in items]

    if expected_count < 0 or len(items) > expected_count:
        raise ValueError("Invalid expected report count.")

    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Duplicate report checks.")

    return ReportMetrics(
        expected=expected_count,
        checked=sum(item.checked for item in items),
        passed=sum(item.passed for item in items),
        failed=sum(item.available and item.checked and not item.passed for item in items),
        unavailable=sum(not item.available for item in items),
    )
