"""Check reports without submitting documents or rerunning OCR/LLM.

By default, the validator recomputes formal outcomes from extracted
analysis fields using the recorded profile configuration.

External reference summaries are optional. Generated ground truth is
not used to judge whether a report faithfully represents analysis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .support.models import AnalysisResult, GeneratedCase, ReportCheckResult, ReportDifference
from .support.report_validation import validate_report

ReportCompletedHandler = Callable[[ReportCheckResult], None]


def run_report_generation(
    cases: Iterable[GeneratedCase],
    analyses: Iterable[AnalysisResult],
    *,
    reference_summaries: Mapping[str, Mapping[str, Any]] | None = None,
    on_report_completed: ReportCompletedHandler | None = None,
) -> list[ReportCheckResult]:
    selected_cases = list(cases)
    case_ids = [case.case_id for case in selected_cases]

    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Duplicate generated case IDs.")

    analysis_by_case: dict[str, AnalysisResult] = {}

    for recorded_analysis in analyses:
        if recorded_analysis.case_id in analysis_by_case:
            raise ValueError(f"Duplicate analysis for {recorded_analysis.case_id!r}.")
        analysis_by_case[recorded_analysis.case_id] = recorded_analysis

    known_ids = set(case_ids)
    unknown_analyses = analysis_by_case.keys() - known_ids

    if unknown_analyses:
        raise ValueError("Unknown analysis case IDs: " + ", ".join(sorted(unknown_analyses)))

    references: Mapping[str, Mapping[str, Any]] = (
        reference_summaries if reference_summaries is not None else {}
    )
    unknown_references = references.keys() - known_ids

    if unknown_references:
        raise ValueError("Unknown reference case IDs: " + ", ".join(sorted(unknown_references)))

    for case_id, reference in references.items():
        if not isinstance(reference, Mapping):
            raise TypeError(f"Reference for {case_id!r} must be a mapping.")

    results: list[ReportCheckResult] = []

    for case in selected_cases:
        analysis = analysis_by_case.get(case.case_id)

        if analysis is None:
            result = ReportCheckResult(
                case_id=case.case_id,
                available=False,
                checked=False,
                differences=(
                    ReportDifference(
                        check="analysis_available",
                        message="No analysis was recorded for this case.",
                    ),
                ),
            )

        elif analysis.document_type != case.document_type:
            result = ReportCheckResult(
                case_id=case.case_id,
                available=analysis.report_available,
                checked=False,
                differences=(
                    ReportDifference(
                        check="document_type",
                        message="Analysis document type does not match the case.",
                        expected=case.document_type,
                        actual=analysis.document_type,
                    ),
                ),
            )

        else:
            result = validate_report(
                analysis,
                reference_summary=references.get(case.case_id),
            )

        results.append(result)

        if on_report_completed is not None:
            on_report_completed(result)

    return results
