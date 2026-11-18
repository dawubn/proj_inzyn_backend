"""Generate documents, execute analyses and evaluate each case immediately."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "tests.effectiveness"

from .support.backend import BackendClient
from .support.catalog import resolve_document_types
from .support.chart import open_chart, save_chart
from .support.generator import generate_documents
from .support.models import (
    AnalysisResult,
    BenchmarkResult,
    DefectKind,
    GeneratedCase,
    MissingFieldsResult,
    RunConfig,
    RunPaths,
    RunStatus,
    utc_now,
)
from .support.results import RunLogger, create_run_directory, save_results
from .test_missing_fields import run_missing_fields
from .test_report_generation import run_report_generation

ReferenceSummaries = dict[str, dict[str, Any]]


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--url",
        default=os.environ.get("TEST_BACKEND_URL", "http://localhost:8000"),
    )
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--min-defects", type=int, default=0)
    parser.add_argument("--max-defects", type=int, default=3)
    parser.add_argument("--kinds", default="missing,invalid_date,grammar")
    parser.add_argument("--types", default="all")
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument("--poll", type=float, default=3.0)
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--reference-summaries", type=Path)

    return parser.parse_args()


def build_config(args: argparse.Namespace) -> RunConfig:
    try:
        kinds = tuple(
            dict.fromkeys(
                DefectKind(value.strip()) for value in args.kinds.split(",") if value.strip()
            )
        )
    except ValueError:
        raise ValueError("Supported defect kinds: missing, invalid_date, grammar.") from None

    selected = (
        () if args.types.strip().lower() == "all" else resolve_document_types(args.types.split(","))
    )

    return RunConfig(
        backend_url=args.url.rstrip("/"),
        document_count=args.count,
        seed=args.seed if args.seed is not None else secrets.randbits(32),
        min_defects=args.min_defects,
        max_defects=args.max_defects,
        defect_kinds=kinds,
        document_types=selected,
        timeout_seconds=args.timeout,
        poll_interval_seconds=args.poll,
        generate_only=args.generate_only,
        preview_chart=not args.no_preview,
    )


def load_reference_summaries(path: Path | None) -> ReferenceSummaries:
    if path is None:
        return {}

    with path.open("r", encoding="utf-8") as stream:
        payload = json.load(stream)

    if not isinstance(payload, dict):
        raise TypeError("Reference summaries must be a JSON object.")

    references: ReferenceSummaries = {}

    for case_id, summary in payload.items():
        if (
            not isinstance(case_id, str)
            or not isinstance(summary, dict)
            or not isinstance(summary.get("rule_results"), list)
        ):
            raise TypeError(
                "Each reference must map a case ID to a summary containing rule_results."
            )

        references[case_id] = summary

    return references


def get_credentials() -> tuple[str | None, str | None, str | None]:
    token = os.environ.get("TEST_ACCESS_TOKEN")

    if token:
        return token, None, None

    email = os.environ.get("TEST_EMAIL")
    password = os.environ.get("TEST_PASSWORD")

    if not email:
        email = input("Test account email: ").strip()

    if password is None:
        password = getpass.getpass("Test account password: ")

    if not email or not password:
        raise ValueError("Test account credentials are required.")

    return None, email, password


def format_percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def write_error(message: str) -> None:
    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()


class BenchmarkRunner:
    """Coordinate generation, backend execution and result persistence."""

    def __init__(
        self,
        config: RunConfig,
        paths: RunPaths,
        references: ReferenceSummaries,
    ) -> None:
        self.config = config
        self.paths = paths
        self.references = references
        self.result = BenchmarkResult(config=config)
        self.logger = RunLogger(paths, self.result)
        self.started = time.monotonic()
        self.interrupted = False
        self.fatal_error = False
        self.output_error = False
        self.chart_path: Path | None = None
        self.cases_by_id: dict[str, GeneratedCase] = {}

    def checkpoint(self) -> None:
        self.result.elapsed_seconds = round(time.monotonic() - self.started, 3)
        save_results(self.paths, self.result)

    def case_completed(
        self,
        analysis: AnalysisResult,
        field_result: MissingFieldsResult,
    ) -> None:
        self.result.analyses.append(analysis)
        self.result.missing_fields_results.append(field_result)

        backend_stop = BackendClient.must_stop(analysis)

        if backend_stop and self.result.stop_reason is None:
            self.result.stop_reason = analysis.error or "Further submissions stopped."

        # Persist the analysis before report validation can fail.
        self.checkpoint()

        case = self.cases_by_id[analysis.case_id]
        case_references = (
            {case.case_id: self.references[case.case_id]} if case.case_id in self.references else {}
        )

        report_results = run_report_generation(
            [case],
            [analysis],
            reference_summaries=case_references,
        )
        report_check = report_results[0]
        self.result.report_results.append(report_check)

        # Persist checks before logging can fail.
        self.checkpoint()

        metrics = field_result.metrics
        self.logger.log(
            "missing_fields",
            f"TP={metrics.tp}; FN={metrics.fn}; FP={metrics.fp}.",
            case_id=case.case_id,
        )

        report_status = (
            "PASS" if report_check.passed else "FAIL" if report_check.checked else "NOT_CHECKED"
        )
        self.logger.log(
            "report_validation",
            f"Report consistency: {report_status}.",
            case_id=case.case_id,
            details={
                "available": report_check.available,
                "checked": report_check.checked,
                "passed": report_check.passed,
                "difference_count": len(report_check.differences),
                "stop_submissions": backend_stop,
            },
        )

    def generate_cases(self) -> None:
        generated = generate_documents(self.paths.documents, self.config)

        if not isinstance(generated, list):
            raise TypeError(
                "generate_documents() must return a list, "
                f"received {type(generated).__name__}. "
                "Check the saved generator.py and its final return cases."
            )

        self.result.cases = generated
        self.cases_by_id = {case.case_id: case for case in generated}

        unknown = self.references.keys() - self.cases_by_id.keys()

        if unknown:
            raise ValueError("Unknown reference case IDs: " + ", ".join(sorted(unknown)))

        self.logger.log(
            "generation",
            f"Generated {len(self.result.cases)} documents.",
        )
        self.checkpoint()

    def execute_backend(self) -> None:
        self.logger.log(
            "run",
            "Real OCR/LLM calls may incur charges. Documents and analyses remain on the backend.",
            level="WARNING",
        )
        token, email, password = get_credentials()

        with BackendClient(
            self.config,
            access_token=token,
            email=email,
            password=password,
            on_event=self.logger,
        ) as backend:
            backend.check_health()
            backend.authenticate()

            run_missing_fields(
                self.result.cases,
                backend,
                self.paths,
                on_case_completed=self.case_completed,
            )

        self.result.status = RunStatus.ABORTED if self.result.stop_reason else RunStatus.COMPLETED

    def execute(self) -> None:
        self.logger.log(
            "run",
            "Benchmark started.",
            details={
                "output_directory": str(self.paths.root),
                "seed": self.config.seed,
                "document_count": self.config.document_count,
            },
        )
        self.checkpoint()
        self.generate_cases()

        if self.config.generate_only:
            self.result.status = RunStatus.GENERATION_ONLY
            self.logger.log("run", "Generation only; backend was not contacted.")
        else:
            self.execute_backend()

    def save_final_chart(self) -> None:
        if not self.result.cases or self.config.generate_only:
            return

        try:
            self.chart_path = save_chart(self.paths, self.result)
            self.logger.log("chart", "Chart saved.")
        except Exception as exc:
            self.output_error = True
            self.logger.log(
                "chart",
                "Chart generation failed.",
                level="ERROR",
                details={"error": f"{type(exc).__name__}: {exc}"},
            )

    def finalize(self) -> None:
        self.result.finished_at = utc_now()
        self.result.elapsed_seconds = round(time.monotonic() - self.started, 3)

        try:
            self.checkpoint()

            if self.result.stop_reason:
                self.logger.log("run", self.result.stop_reason, level="ERROR")

            self.save_final_chart()
            self.checkpoint()
        except Exception as exc:
            self.output_error = True
            write_error(f"Final output error: {type(exc).__name__}: {exc}")

    def preview_chart(self) -> None:
        if self.chart_path is None or not self.config.preview_chart:
            return

        try:
            open_chart(self.chart_path)
        except Exception as exc:
            write_error(f"Chart saved; preview unavailable: {exc}")

    def run(self) -> int:
        try:
            self.execute()
        except KeyboardInterrupt:
            self.interrupted = True
            self.result.status = RunStatus.INTERRUPTED
            self.result.stop_reason = (
                "Interrupted by user. Submitted analyses may still be running."
            )
        except Exception as exc:
            self.fatal_error = True
            self.result.status = RunStatus.ABORTED
            self.result.stop_reason = f"{type(exc).__name__}: {exc}"
        finally:
            self.finalize()

        write_summary(self.paths, self.result)
        self.preview_chart()

        if self.interrupted:
            return 130

        if self.fatal_error or self.output_error or self.result.status == RunStatus.ABORTED:
            return 1

        if self.config.generate_only:
            return 0

        return 0 if benchmark_passed(self.result) else 1


def write_summary(paths: RunPaths, result: BenchmarkResult) -> None:
    processed = sum(item.processed for item in result.analyses)
    lines = [
        "",
        f"Run status: {result.status.value}",
        f"Results: {paths.root}",
        f"Generated: {len(result.cases)} / {result.config.document_count}; processed: {processed}",
    ]

    if not result.config.generate_only:
        metrics = result.missing_fields_metrics
        reports = result.report_metrics

        lines.extend(
            [
                f"TP: {metrics.tp} | FN: {metrics.fn} | FP: {metrics.fp}",
                f"Recall: {format_percentage(metrics.recall)} | "
                f"Precision: {format_percentage(metrics.precision)}",
                f"Reports passed: {reports.passed} / {reports.processed}; "
                f"checked: {reports.checked}; "
                f"consistency: {format_percentage(reports.success_rate)}",
            ]
        )

    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def benchmark_passed(result: BenchmarkResult) -> bool:
    execution_complete = len(result.analyses) == len(result.cases) and all(
        item.processed
        and item.report_available
        and item.pdf_downloaded
        and item.error is None
        and item.export_error is None
        for item in result.analyses
    )

    detection_passed = len(result.missing_fields_results) == len(result.cases) and all(
        assessment.exact_match
        for item in result.missing_fields_results
        for assessment in item.assessments
        if assessment.absence_scored or assessment.defect_kind == DefectKind.INVALID_DATE
    )

    reports_passed = (
        len(result.report_results) == len(result.cases)
        and result.report_metrics.expected > 0
        and result.report_metrics.passed == result.report_metrics.expected
    )

    return execution_complete and detection_passed and reports_passed


def main() -> int:
    args = parse_arguments()

    try:
        config = build_config(args)
        references = load_reference_summaries(args.reference_summaries)
        paths = create_run_directory(args.output)
    except (ValueError, TypeError, OSError) as exc:
        write_error(f"Configuration error: {exc}")
        return 2

    return BenchmarkRunner(config, paths, references).run()


if __name__ == "__main__":
    raise SystemExit(main())
