"""Persist benchmark results with bounded retries for locked files."""

from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import time
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .models import BenchmarkResult, LogEvent, ReportMetrics, RunPaths
from .scoring import aggregate_missing_fields, metrics_by_document_type

DEFAULT_RESULT_DIRECTORY = Path(__file__).resolve().parents[1] / "result"
REPLACE_ATTEMPTS = 8
LOG_WARNING_INTERVAL = 15.0


def _compound_json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: to_json_value(getattr(value, item.name)) for item in fields(value)}

    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings.")
        return {key: to_json_value(item) for key, item in value.items()}

    if isinstance(value, list | tuple):
        return [to_json_value(item) for item in value]

    raise TypeError(f"Cannot serialize {type(value).__name__}.")


def to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return to_json_value(value.value)

    if value is None or isinstance(value, str | bool | int):
        return value

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Cannot serialize a non-finite number.")
        return value

    if isinstance(value, Path):
        return str(value)

    if isinstance(value, datetime | date):
        return value.isoformat()

    return _compound_json_value(value)


def _replace_snapshot(temporary: Path, destination: Path) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temporary.replace(destination)
        except OSError as exc:
            retryable = isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in {
                5,
                32,
                33,
            }

            if not retryable or attempt == REPLACE_ATTEMPTS - 1:
                raise OSError(
                    f"Cannot replace {destination}. "
                    f"New snapshot retained at {temporary}. "
                    f"Original error: {exc}"
                ) from exc

            time.sleep(min(0.1 * (2**attempt), 1.0))
        else:
            return


def save_json(path: Path, data: Any) -> None:
    """Retry permission/sharing failures without truncating the old file.

    If replacement fails permanently, retain the new temporary snapshot
    and raise an error identifying its location.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    serialized = (
        json.dumps(
            to_json_value(data),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )

    temporary: Path | None = None
    written = False

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(serialized)
            stream.flush()
            os.fsync(stream.fileno())

        written = True
        _replace_snapshot(temporary, path)
    finally:
        if temporary is not None and not written:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def create_run_directory(
    base_directory: Path | None = None,
    *,
    run_date: date | None = None,
) -> RunPaths:
    base = Path(base_directory) if base_directory is not None else DEFAULT_RESULT_DIRECTORY
    base.mkdir(parents=True, exist_ok=True)

    day = run_date or datetime.now().astimezone().date()
    name = f"test-{day:%Y-%m-%d}"
    number = 1

    while True:
        suffix = "" if number == 1 else f"-{number:02d}"
        root = base / f"{name}{suffix}"

        try:
            root.mkdir()
            break
        except FileExistsError:
            number += 1

    paths = RunPaths(root=root)
    paths.documents.mkdir()
    return paths


def _index_by_case(
    items: list[Any],
    collection_name: str,
) -> dict[str, Any]:
    indexed: dict[str, Any] = {}

    for item in items:
        if item.case_id in indexed:
            raise ValueError(f"Duplicate case {item.case_id!r} in {collection_name}.")
        indexed[item.case_id] = item

    return indexed


def _report_state(result: BenchmarkResult) -> dict[str, int]:
    cases = _index_by_case(result.cases, "cases")
    analyses = _index_by_case(result.analyses, "analyses")
    checks = _index_by_case(result.report_results, "report_results")

    unknown = (analyses.keys() | checks.keys()) - cases.keys()

    if unknown:
        raise ValueError("Unknown case IDs: " + ", ".join(sorted(unknown)))

    state = {
        "expected": len(cases),
        "available": 0,
        "checked": 0,
        "passed": 0,
        "failed": 0,
        "unavailable": 0,
        "pending": 0,
        "available_unchecked": 0,
    }

    for case_id in cases:
        analysis = analyses.get(case_id)
        check = checks.get(case_id)

        available = (
            analysis.report_available if analysis is not None else bool(check and check.available)
        )

        if check is not None and check.available != available:
            raise ValueError(f"Inconsistent report availability for {case_id!r}.")

        if available:
            state["available"] += 1

            if check is not None and check.checked:
                state["checked"] += 1
                state["passed" if check.passed else "failed"] += 1
            else:
                state["available_unchecked"] += 1
        elif analysis is not None or check is not None:
            state["unavailable"] += 1
        else:
            state["pending"] += 1

    return state


def refresh_metrics(result: BenchmarkResult) -> None:
    result.missing_fields_metrics = aggregate_missing_fields(result.missing_fields_results)

    state = _report_state(result)

    result.report_metrics = ReportMetrics(
        expected=state["expected"],
        processed=sum(item.processed for item in result.analyses),
        checked=state["checked"],
        passed=state["passed"],
        failed=state["failed"],
        unavailable=state["unavailable"],
    )


def build_result_payload(result: BenchmarkResult) -> dict[str, Any]:
    refresh_metrics(result)

    cases = _index_by_case(result.cases, "cases")
    analyses = _index_by_case(result.analyses, "analyses")
    missing = _index_by_case(
        result.missing_fields_results,
        "missing_fields_results",
    )
    reports = _index_by_case(result.report_results, "report_results")

    unknown = missing.keys() - cases.keys()

    if unknown:
        raise ValueError("Unknown field assessment cases: " + ", ".join(sorted(unknown)))

    processed_count = sum(item.processed for item in result.analyses)

    report_state = _report_state(result)
    report_summary: dict[str, Any] = dict(report_state)
    report_summary["processed"] = processed_count
    report_summary["success_rate"] = (
        report_state["passed"] / processed_count if processed_count else None
    )
    report_summary["checked_pass_rate"] = (
        report_state["passed"] / report_state["checked"] if report_state["checked"] else None
    )

    documents: list[dict[str, Any]] = []

    for case in result.cases:
        analysis = analyses.get(case.case_id)
        field_result = missing.get(case.case_id)
        report_check = reports.get(case.case_id)

        documents.append(
            {
                "case_id": case.case_id,
                "document_type": case.document_type,
                "filename": case.filename,
                "metadata": case.metadata,
                "defects": case.defects,
                "expected_fields": case.expected_fields,
                "analysis_result": analysis,
                "missing_fields_result": (
                    {
                        "assessments": field_result.assessments,
                        "metrics": field_result.metrics.to_dict(),
                    }
                    if field_result is not None
                    else None
                ),
                "report_check": (
                    {
                        "available": report_check.available,
                        "checked": report_check.checked,
                        "passed": report_check.passed,
                        "differences": report_check.differences,
                    }
                    if report_check is not None
                    else None
                ),
            }
        )

    return {
        "schema_version": "1.2",
        "configuration": result.config,
        "execution": {
            "status": result.status,
            "started_at": result.started_at,
            "finished_at": result.finished_at,
            "elapsed_seconds": result.elapsed_seconds,
            "stop_reason": result.stop_reason,
            "documents_requested": result.config.document_count,
            "documents_generated": len(result.cases),
            "documents_recorded": len(result.analyses),
            "documents_submitted": sum(item.document_id is not None for item in result.analyses),
            "documents_processed": processed_count,
            "json_reports_available": sum(item.report_available for item in result.analyses),
            "pdf_reports_downloaded": sum(item.pdf_downloaded for item in result.analyses),
            "errors": [
                {
                    "case_id": item.case_id,
                    "stage": item.stage,
                    "error": item.error,
                    "export_error": item.export_error,
                }
                for item in result.analyses
                if item.error or item.export_error
            ],
        },
        "summary": {
            "missing_fields": result.missing_fields_metrics.to_dict(),
            "reports": report_summary,
        },
        "metrics_scope": {
            "missing_fields": {
                "recall": "tp / (tp + fn)",
                "precision": "tp / (tp + fp)",
                "unit": "field",
                "note": (
                    "Metrics cover recorded assessments only. "
                    "Unknown outcomes are not successful detections. "
                    "Invalid dates, advisory and visual fields, and "
                    "signatures are excluded from absence metrics."
                ),
            },
            "reports": {
                "success_rate": "passed / processed",
                "checked_pass_rate": "passed / checked",
                "undefined_value": None,
                "unit": "document",
                "note": (
                    "Report consistency uses processed documents as "
                    "the denominator, not all generated documents. "
                    "Missing or unchecked reports for processed "
                    "documents do not count as passed. Success rate "
                    "is null when no document was processed. "
                    "Pending, unavailable and available-but-unchecked "
                    "reports are recorded separately. "
                    "Validation uses supplied reference results or "
                    "results recomputed from extracted analysis fields "
                    "with the recorded profile configuration. "
                    "Profile selection and full PDF content are "
                    "not independently verified."
                ),
            },
        },
        "by_document_type": {
            name: metrics.to_dict()
            for name, metrics in metrics_by_document_type(result.missing_fields_results).items()
        },
        "documents": documents,
    }


def build_log_payload(result: BenchmarkResult) -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "configuration": result.config,
        "status": result.status,
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "elapsed_seconds": result.elapsed_seconds,
        "stop_reason": result.stop_reason,
        "events": result.events,
    }


def _write_warning(message: str) -> None:
    sys.stderr.write(f"WARNING: {message}\n")
    sys.stderr.flush()


def save_results(paths: RunPaths, result: BenchmarkResult) -> None:
    # Result snapshot failures remain fatal: do not silently lose results.
    save_json(paths.result, build_result_payload(result))

    try:
        save_json(paths.log, build_log_payload(result))
    except OSError as exc:
        _write_warning(f"Log snapshot could not be replaced: {exc}")


class RunLogger:
    def __init__(
        self,
        paths: RunPaths,
        result: BenchmarkResult,
        *,
        console: bool = True,
    ) -> None:
        self.paths = paths
        self.result = result
        self.console = console
        self._last_flush_warning = 0.0

    def __call__(self, event: LogEvent) -> None:
        self.result.events.append(event)

        if self.console:
            case = f" [{event.case_id}]" if event.case_id else ""
            sys.stdout.write(
                f"[{event.timestamp}] {event.level} [{event.stage}]{case} {event.message}\n"
            )
            sys.stdout.flush()

        self.flush()

    def log(
        self,
        stage: str,
        message: str,
        *,
        level: str = "INFO",
        case_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self(
            LogEvent(
                stage=stage,
                message=message,
                level=level,
                case_id=case_id,
                details=details or {},
            )
        )

    def flush(self) -> None:
        try:
            save_json(self.paths.log, build_log_payload(self.result))
        except OSError as exc:
            now = time.monotonic()

            if now - self._last_flush_warning >= LOG_WARNING_INTERVAL:
                _write_warning(
                    f"Log write failed; events remain in memory. Next flush will retry. {exc}"
                )
                self._last_flush_warning = now
