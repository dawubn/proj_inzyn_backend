"""Execute cases and evaluate missing-field detection.

Detection is scored from the formal rule results returned in report JSON.
Report-generation consistency is evaluated separately.

This module is invoked by run.py. Importing or collecting it with pytest
does not submit documents or contact external services.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from .support.backend import BackendClient
from .support.models import AnalysisResult, GeneratedCase, MissingFieldsResult, RunPaths, utc_now
from .support.scoring import evaluate_fields, extract_rule_results

CaseCompletedHandler = Callable[
    [AnalysisResult, MissingFieldsResult],
    None,
]


def evaluate_case(
    case: GeneratedCase,
    analysis: AnalysisResult,
) -> MissingFieldsResult:
    if analysis.case_id != case.case_id:
        raise ValueError(f"Case ID mismatch: {case.case_id!r} != {analysis.case_id!r}.")

    if analysis.document_type != case.document_type:
        raise ValueError(f"Document type mismatch for {case.case_id!r}.")

    processing_error: str | None = None

    if not analysis.processed:
        processing_error = analysis.error or "Document analysis did not complete."
    elif not analysis.report_available:
        processing_error = analysis.error or "Formal rule results are unavailable: no JSON report."

    rule_results = extract_rule_results(analysis.report_payload)

    return evaluate_fields(
        case=case,
        rule_results=rule_results,
        processing_error=processing_error,
    )


def run_missing_fields(
    cases: Iterable[GeneratedCase],
    backend: BackendClient,
    paths: RunPaths,
    *,
    on_case_completed: CaseCompletedHandler | None = None,
) -> tuple[list[AnalysisResult], list[MissingFieldsResult]]:
    """Process each document once and score its missing fields.

    A fatal backend failure prevents further submissions. Remaining
    documents receive explicit not-submitted results instead of silently
    disappearing from the benchmark.

    KeyboardInterrupt propagates to run.py. Results already delivered
    through the callback can be checkpointed there.
    """

    selected_cases = list(cases)
    identifiers = [case.case_id for case in selected_cases]

    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Generated cases contain duplicate case IDs.")

    analyses: list[AnalysisResult] = []
    field_results: list[MissingFieldsResult] = []

    stop_reason: str | None = None

    for case in selected_cases:
        if stop_reason is None:
            analysis = backend.process_case(case, paths)
        else:
            now = utc_now()

            analysis = AnalysisResult(
                case_id=case.case_id,
                document_type=case.document_type,
                stage="not_submitted",
                error=f"Not submitted: {stop_reason}",
                started_at=now,
                finished_at=now,
            )

        field_result = evaluate_case(case, analysis)

        analyses.append(analysis)
        field_results.append(field_result)

        if stop_reason is None and backend.must_stop(analysis):
            stop_reason = analysis.error or "Backend failure requires stopping further submissions."

        if on_case_completed is not None:
            on_case_completed(analysis, field_result)

    return analyses, field_results
