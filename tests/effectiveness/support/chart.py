"""Render detection metrics and report-validation progress."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .models import BenchmarkResult, MissingFieldsMetrics, ReportMetrics, RunPaths
from .results import build_result_payload

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure


CHART_LEFT = 0.23
REPLACE_ATTEMPTS = 8


@dataclass(frozen=True, slots=True)
class ChartData:
    """Metrics and execution counts displayed on the chart."""

    missing_fields: MissingFieldsMetrics
    reports: ReportMetrics
    document_count: int
    run_status: str
    processed_count: int = 0
    assessed_count: int = 0
    reports_available: int = 0
    reports_pending: int = 0
    reports_unchecked: int = 0

    @property
    def report_rate(self) -> float | None:
        if not self.processed_count:
            return None
        return self.reports.passed / self.processed_count

    @property
    def scores(self) -> tuple[float | None, ...]:
        return (
            self.missing_fields.recall,
            self.missing_fields.precision,
            self.report_rate,
        )

    def validate(self) -> None:
        counts = (
            self.document_count,
            self.processed_count,
            self.assessed_count,
            self.reports_available,
            self.reports_pending,
            self.reports_unchecked,
            self.reports.passed,
            self.reports.checked,
            self.reports.failed,
            self.reports.unavailable,
        )

        if any(value < 0 for value in counts):
            raise ValueError("Chart counts cannot be negative.")

        if self.processed_count > self.document_count:
            raise ValueError("Processed count exceeds generated document count.")

        if self.reports.passed > self.processed_count:
            raise ValueError("Passed reports exceed processed document count.")

        for score in self.scores:
            if score is not None and not 0 <= score <= 1:
                raise ValueError(f"Invalid chart score: {score}")


def _format_percentage(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.2f}%"


def _draw_bars(ax: Axes, data: ChartData) -> None:
    colors = ("#2563EB", "#0D9488", "#7C3AED")

    for position, (score, color) in enumerate(zip(data.scores, colors, strict=True)):
        ax.barh(
            position,
            100,
            height=0.44,
            color="#EEF2F6",
            zorder=1,
        )

        if score is not None:
            ax.barh(
                position,
                score * 100,
                height=0.44,
                color=color,
                zorder=2,
            )

        ax.text(
            103,
            position,
            _format_percentage(score),
            va="center",
            ha="left",
            fontsize=14,
            fontweight="bold",
            color="#111827",
        )


def _configure_axes(ax: Axes) -> None:
    from matplotlib.ticker import PercentFormatter

    labels = ("Recall", "Precision", "Report consistency")

    ax.set_facecolor("white")
    ax.set_yticks(range(len(labels)), labels, fontsize=12)
    ax.set_ylim(2.6, -0.6)
    ax.set_xlim(0, 119)
    ax.set_xticks((0, 25, 50, 75, 100))
    ax.xaxis.set_major_formatter(PercentFormatter(xmax=100))
    ax.grid(
        axis="x",
        color="#D1D5DB",
        alpha=0.6,
        linewidth=0.8,
    )
    ax.set_axisbelow(True)
    ax.tick_params(axis="both", length=0, pad=10, labelsize=11)

    for spine in ax.spines.values():
        spine.set_visible(False)


def _draw_titles(fig: Figure, ax: Axes, data: ChartData) -> None:
    fig.suptitle(
        "Effectiveness results",
        x=CHART_LEFT,
        y=0.96,
        ha="left",
        fontsize=22,
        fontweight="bold",
        color="#111827",
    )
    ax.set_title(
        f"Generated: {data.document_count}"
        f" | Processed: {data.processed_count}"
        f" | Assessed: {data.assessed_count}"
        f" | Status: {data.run_status}",
        loc="left",
        fontsize=10,
        color="#6B7280",
        pad=20,
    )


def _draw_summary(fig: Figure, data: ChartData) -> None:
    metrics = data.missing_fields
    reports = data.reports

    fig.text(
        CHART_LEFT,
        0.31,
        f"TP: {metrics.tp}     FN: {metrics.fn}     FP: {metrics.fp}",
        fontsize=16,
        fontweight="bold",
        color="#111827",
    )
    fig.text(
        CHART_LEFT,
        0.265,
        f"Reports passed: {reports.passed} / {data.processed_count}"
        f"  ({_format_percentage(data.report_rate)})",
        fontsize=13,
        color="#111827",
    )
    fig.text(
        CHART_LEFT,
        0.225,
        f"Available: {data.reports_available}"
        f" | Checked: {reports.checked}"
        f" | Failed checks: {reports.failed}",
        fontsize=10,
        color="#4B5563",
    )
    fig.text(
        CHART_LEFT,
        0.19,
        f"Unavailable: {reports.unavailable}"
        f" | Pending: {data.reports_pending}"
        f" | Available but unchecked: {data.reports_unchecked}",
        fontsize=10,
        color="#4B5563",
    )


def _draw_notes(fig: Figure, data: ChartData) -> None:
    fig.text(
        CHART_LEFT,
        0.15,
        "Recall = TP / (TP + FN)     Precision = TP / (TP + FP)",
        fontsize=10,
        color="#4B5563",
    )
    fig.text(
        CHART_LEFT,
        0.115,
        f"Unresolved present fields: {data.missing_fields.unresolved_present}"
        f" | Excluded assessments: {data.missing_fields.excluded}",
        fontsize=9,
        color="#6B7280",
    )
    fig.text(
        CHART_LEFT,
        0.075,
        "Detection metrics cover recorded field assessments."
        "\nReport consistency = passed / processed documents;"
        " N/A when no document was processed.",
        fontsize=8.5,
        color="#6B7280",
        linespacing=1.4,
    )
    fig.text(
        CHART_LEFT,
        0.025,
        "JSON results are compared with reference or recomputed analysis."
        "\nRecorded-profile selection and full PDF content are not verified.",
        fontsize=8.5,
        color="#6B7280",
        linespacing=1.4,
    )


def _replace_chart(temporary_path: Path, destination: Path) -> None:
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temporary_path.replace(destination)
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(min(0.1 * (2**attempt), 1.0))
        else:
            return


def _save_figure(fig: Figure, destination: Path) -> None:
    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            prefix=".chart.",
            suffix=".png",
            dir=destination.parent,
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)

        fig.savefig(
            temporary_path,
            format="png",
            dpi=180,
            facecolor="white",
        )

        if temporary_path.stat().st_size == 0:
            raise RuntimeError("Generated chart is empty.")

        _replace_chart(temporary_path, destination)
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


def _render_chart(destination: Path, data: ChartData) -> Path:
    import matplotlib

    matplotlib.use("Agg")

    import matplotlib.pyplot as plt

    data.validate()
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(13, 8.2))

    try:
        fig.patch.set_facecolor("white")
        _configure_axes(ax)
        _draw_bars(ax, data)
        _draw_titles(fig, ax, data)
        _draw_summary(fig, data)
        _draw_notes(fig, data)
        fig.subplots_adjust(
            left=CHART_LEFT,
            right=0.96,
            top=0.80,
            bottom=0.42,
        )
        _save_figure(fig, destination)
        return destination

    finally:
        plt.close(fig)


# Preserve the existing keyword-based API used by benchmark callers.
def create_chart(  # noqa: PLR0913
    destination: Path,
    *,
    missing_fields: MissingFieldsMetrics,
    reports: ReportMetrics,
    document_count: int,
    run_status: str,
    processed_count: int = 0,
    assessed_count: int = 0,
    reports_available: int = 0,
    reports_pending: int = 0,
    reports_unchecked: int = 0,
) -> Path:
    data = ChartData(
        missing_fields=missing_fields,
        reports=reports,
        document_count=document_count,
        run_status=run_status,
        processed_count=processed_count,
        assessed_count=assessed_count,
        reports_available=reports_available,
        reports_pending=reports_pending,
        reports_unchecked=reports_unchecked,
    )
    return _render_chart(destination, data)


def save_chart(paths: RunPaths, result: BenchmarkResult) -> Path:
    payload = build_result_payload(result)
    report_summary = payload["summary"]["reports"]

    return create_chart(
        paths.chart,
        missing_fields=result.missing_fields_metrics,
        reports=result.report_metrics,
        document_count=len(result.cases),
        run_status=result.status.value,
        processed_count=payload["execution"]["documents_processed"],
        assessed_count=len(result.missing_fields_results),
        reports_available=report_summary["available"],
        reports_pending=report_summary["pending"],
        reports_unchecked=report_summary["available_unchecked"],
    )


def open_chart(path: Path) -> None:
    path = Path(path).resolve()

    if not path.is_file():
        raise FileNotFoundError(f"Chart does not exist: {path}")

    if path.suffix.lower() != ".png":
        raise ValueError(f"Chart preview requires a PNG file: {path}")

    if sys.platform == "win32":
        # Open the validated local PNG through its registered Windows viewer.
        os.startfile(str(path))  # noqa: S606
        return

    command = "open" if sys.platform == "darwin" else "xdg-open"
    executable = shutil.which(command)

    if executable is None:
        raise FileNotFoundError(f"Chart viewer command is unavailable: {command}")

    # No shell: use the resolved executable and a separate absolute PNG argument.
    subprocess.Popen([executable, str(path)])  # noqa: S603
