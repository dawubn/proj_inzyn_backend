"""Shared data structures for the effectiveness benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    ERROR = "error"


class DefectKind(StrEnum):
    MISSING = "missing"
    INVALID_DATE = "invalid_date"
    GRAMMAR = "grammar"


class Classification(StrEnum):
    TP = "TP"
    FN = "FN"
    FP = "FP"
    TN = "TN"
    UNRESOLVED_PRESENT = "UNRESOLVED_PRESENT"
    NOT_SCORED = "NOT_SCORED"


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    GENERATION_ONLY = "generation_only"
    ABORTED = "aborted"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class RunConfig:
    backend_url: str = "http://localhost:8000"
    document_count: int = 100
    seed: int = 12345
    min_defects: int = 0
    max_defects: int = 3

    defect_kinds: tuple[DefectKind, ...] = (
        DefectKind.MISSING,
        DefectKind.INVALID_DATE,
        DefectKind.GRAMMAR,
    )

    document_types: tuple[str, ...] = ()
    timeout_seconds: float = 900.0
    poll_interval_seconds: float = 3.0
    generate_only: bool = False
    preview_chart: bool = True

    def __post_init__(self) -> None:
        if not self.backend_url.strip():
            raise ValueError("backend_url cannot be empty.")

        if self.document_count <= 0:
            raise ValueError("document_count must be positive.")

        if self.min_defects < 0:
            raise ValueError("min_defects cannot be negative.")

        if self.max_defects < self.min_defects:
            raise ValueError("max_defects must be >= min_defects.")

        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")

        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive.")


@dataclass(frozen=True, slots=True)
class RunPaths:
    root: Path

    @property
    def documents(self) -> Path:
        return self.root / "documents"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def log(self) -> Path:
        return self.root / "log.json"

    @property
    def result(self) -> Path:
        return self.root / "report_result.json"

    @property
    def chart(self) -> Path:
        return self.root / "chart.png"


@dataclass(frozen=True, slots=True)
class FieldDefinition:
    key: str
    label: str
    kind: str
    advisory: bool = False


@dataclass(frozen=True, slots=True)
class GeneratedDefect:
    kind: DefectKind
    field_key: str | None = None
    description: str = ""
    mutation_style: str | None = None


@dataclass(frozen=True, slots=True)
class ExpectedField:
    field_key: str
    label: str
    expected: Outcome

    defect_kind: DefectKind | None = None
    scored: bool = True
    absence_scored: bool = True
    exclusion_reason: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedCase:
    case_id: str
    document_type: str
    filename: str
    expected_fields: tuple[ExpectedField, ...]

    defects: tuple[GeneratedDefect, ...] = ()
    metadata: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class AnalysisResult:
    case_id: str
    document_type: str

    document_id: str | None = None
    analysis_id: str | None = None

    analysis_payload: JsonObject | None = None
    report_payload: JsonObject | None = None
    report_pdf_path: Path | None = None

    processed: bool = False
    pdf_downloaded: bool = False

    stage: str = "not_submitted"
    error: str | None = None
    export_error: str | None = None

    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    elapsed_seconds: float = 0.0

    @property
    def analysis_available(self) -> bool:
        return self.analysis_payload is not None

    @property
    def report_available(self) -> bool:
        return self.report_payload is not None


@dataclass(frozen=True, slots=True)
class FieldAssessment:
    case_id: str
    document_type: str
    field_key: str
    label: str

    expected: Outcome
    actual: Outcome
    classification: Classification

    absence_scored: bool
    exact_match: bool

    defect_kind: DefectKind | None = None
    reason_codes: tuple[str, ...] = ()
    processing_error: str | None = None


@dataclass(frozen=True, slots=True)
class MissingFieldsMetrics:
    tp: int = 0
    fn: int = 0
    fp: int = 0
    tn: int = 0
    unresolved_present: int = 0
    excluded: int = 0

    @property
    def actual_missing_fields(self) -> int:
        return self.tp + self.fn

    @property
    def reported_missing_fields(self) -> int:
        return self.tp + self.fp

    @property
    def recall(self) -> float | None:
        denominator = self.actual_missing_fields
        return self.tp / denominator if denominator else None

    @property
    def precision(self) -> float | None:
        denominator = self.reported_missing_fields
        return self.tp / denominator if denominator else None

    def to_dict(self) -> JsonObject:
        return {
            "tp": self.tp,
            "fn": self.fn,
            "fp": self.fp,
            "tn": self.tn,
            "unresolved_present": self.unresolved_present,
            "excluded": self.excluded,
            "actual_missing_fields": self.actual_missing_fields,
            "reported_missing_fields": self.reported_missing_fields,
            "recall": self.recall,
            "precision": self.precision,
        }


@dataclass(frozen=True, slots=True)
class MissingFieldsResult:
    case_id: str
    assessments: tuple[FieldAssessment, ...]
    metrics: MissingFieldsMetrics


@dataclass(frozen=True, slots=True)
class ReportDifference:
    check: str
    message: str

    field_key: str | None = None
    expected: Any = None
    actual: Any = None


@dataclass(frozen=True, slots=True)
class ReportCheckResult:
    case_id: str
    available: bool
    checked: bool
    differences: tuple[ReportDifference, ...] = ()

    @property
    def passed(self) -> bool:
        return self.available and self.checked and not self.differences


@dataclass(frozen=True, slots=True)
class ReportMetrics:
    expected: int = 0
    checked: int = 0
    passed: int = 0
    failed: int = 0
    unavailable: int = 0
    processed: int = 0

    @property
    def success_rate(self) -> float | None:
        return self.passed / self.expected if self.expected else None

    def to_dict(self) -> JsonObject:
        return {
            "expected": self.expected,
            "checked": self.checked,
            "passed": self.passed,
            "failed": self.failed,
            "unavailable": self.unavailable,
            "success_rate": self.success_rate,
        }


@dataclass(frozen=True, slots=True)
class LogEvent:
    stage: str
    message: str

    level: str = "INFO"
    case_id: str | None = None
    timestamp: str = field(default_factory=utc_now)
    details: JsonObject = field(default_factory=dict)


@dataclass(slots=True)
class BenchmarkResult:
    config: RunConfig
    status: RunStatus = RunStatus.RUNNING

    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    elapsed_seconds: float = 0.0
    stop_reason: str | None = None

    cases: list[GeneratedCase] = field(default_factory=list)
    analyses: list[AnalysisResult] = field(default_factory=list)

    missing_fields_results: list[MissingFieldsResult] = field(default_factory=list)
    report_results: list[ReportCheckResult] = field(default_factory=list)

    missing_fields_metrics: MissingFieldsMetrics = field(default_factory=MissingFieldsMetrics)
    report_metrics: ReportMetrics = field(default_factory=ReportMetrics)

    events: list[LogEvent] = field(default_factory=list)
