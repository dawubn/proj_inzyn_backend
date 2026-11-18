"""Field-level scoring independent of HTTP, PDF generation and file output."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from typing import Any

from .models import (
    Classification,
    DefectKind,
    ExpectedField,
    FieldAssessment,
    GeneratedCase,
    JsonObject,
    MissingFieldsMetrics,
    MissingFieldsResult,
    Outcome,
)


def extract_rule_results(
    payload: Mapping[str, Any] | None,
) -> list[JsonObject]:
    """Read rule results from a summary or a report containing a summary.

    Missing or malformed data is not interpreted as successful validation.
    This function does not substitute report data for analysis data.
    """

    if not isinstance(payload, Mapping):
        return []

    summary = payload if "rule_results" in payload else payload.get("summary")

    if not isinstance(summary, Mapping):
        return []

    results = summary.get("rule_results")

    if not isinstance(results, list):
        return []

    return [dict(result) for result in results if isinstance(result, Mapping)]


def _actual_outcome(
    matches: list[JsonObject],
    processing_error: str | None,
) -> tuple[Outcome, str | None]:
    if processing_error:
        return Outcome.ERROR, processing_error

    if not matches:
        return Outcome.ERROR, "Missing rule result."

    if len(matches) != 1:
        return Outcome.ERROR, f"Expected one rule result, received {len(matches)}."

    raw_outcome = matches[0].get("outcome")

    if not isinstance(raw_outcome, str):
        return Outcome.ERROR, "Rule outcome is missing or not a string."

    try:
        return Outcome(raw_outcome), None
    except ValueError:
        return Outcome.ERROR, f"Unsupported rule outcome: {raw_outcome!r}."


def classify_absence(
    expected: ExpectedField,
    actual: Outcome,
) -> Classification:
    """Treat FAIL as a reported defect, preserving unresolved outcomes."""

    if (
        not expected.scored
        or not expected.absence_scored
        or expected.defect_kind == DefectKind.INVALID_DATE
        or expected.expected not in {Outcome.PASS, Outcome.FAIL}
    ):
        return Classification.NOT_SCORED

    if expected.defect_kind == DefectKind.MISSING:
        return Classification.TP if actual == Outcome.FAIL else Classification.FN

    if actual == Outcome.FAIL:
        return Classification.FP

    if actual == Outcome.PASS:
        return Classification.TN

    return Classification.UNRESOLVED_PRESENT


def calculate_metrics(
    assessments: Iterable[FieldAssessment],
) -> MissingFieldsMetrics:
    counts = Counter(assessment.classification for assessment in assessments)

    return MissingFieldsMetrics(
        tp=counts[Classification.TP],
        fn=counts[Classification.FN],
        fp=counts[Classification.FP],
        tn=counts[Classification.TN],
        unresolved_present=counts[Classification.UNRESOLVED_PRESENT],
        excluded=counts[Classification.NOT_SCORED],
    )


def _collect_reason_codes(
    matches: Iterable[JsonObject],
    assessment_error: str | None,
) -> tuple[str, ...]:
    codes: dict[str, None] = {}

    for result in matches:
        code = result.get("reason_code")

        if isinstance(code, str) and code:
            codes[code] = None

    reason_codes = tuple(codes)

    if assessment_error:
        reason_codes += ("BENCHMARK_RESULT_UNAVAILABLE",)

    return reason_codes


def evaluate_fields(
    case: GeneratedCase,
    rule_results: Iterable[Mapping[str, Any]],
    processing_error: str | None = None,
) -> MissingFieldsResult:
    """Compare generated ground truth against backend rule results.

    The caller supplies the actual rule-result collection explicitly.
    No backend request or report download is performed here.
    """

    grouped: dict[str, list[JsonObject]] = defaultdict(list)

    for result in rule_results:
        if not isinstance(result, Mapping):
            continue

        field_key = result.get("field_name")

        if isinstance(field_key, str) and field_key:
            grouped[field_key].append(dict(result))

    assessments: list[FieldAssessment] = []

    for expected in case.expected_fields:
        matches = grouped.get(expected.field_key, [])
        actual, assessment_error = _actual_outcome(matches, processing_error)
        classification = classify_absence(expected, actual)

        assessments.append(
            FieldAssessment(
                case_id=case.case_id,
                document_type=case.document_type,
                field_key=expected.field_key,
                label=expected.label,
                expected=expected.expected,
                actual=actual,
                classification=classification,
                absence_scored=classification != Classification.NOT_SCORED,
                exact_match=assessment_error is None and actual == expected.expected,
                defect_kind=expected.defect_kind,
                reason_codes=_collect_reason_codes(matches, assessment_error),
                processing_error=assessment_error,
            )
        )

    return MissingFieldsResult(
        case_id=case.case_id,
        assessments=tuple(assessments),
        metrics=calculate_metrics(assessments),
    )


def aggregate_missing_fields(
    results: Iterable[MissingFieldsResult],
) -> MissingFieldsMetrics:
    return calculate_metrics(assessment for result in results for assessment in result.assessments)


def metrics_by_document_type(
    results: Iterable[MissingFieldsResult],
) -> dict[str, MissingFieldsMetrics]:
    grouped: dict[str, list[FieldAssessment]] = defaultdict(list)

    for result in results:
        for assessment in result.assessments:
            grouped[assessment.document_type].append(assessment)

    return {
        document_type: calculate_metrics(assessments)
        for document_type, assessments in sorted(grouped.items())
    }
