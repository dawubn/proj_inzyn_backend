"""PDF export of a finished analysis: formal validation and legal analysis in one document."""

from __future__ import annotations

import io
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import fitz
import structlog
from PIL import Image

from app.core.exceptions import NotFoundError
from app.models.analysis_report import AnalysisReport
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.services.page_preview import PagePreviewService, PreviewVariant

logger = structlog.get_logger(__name__)

TIMEZONE = ZoneInfo("Europe/Warsaw")
PAGE_MARGIN = 48
RULE_RESULTS_SCHEMA_VERSION = 2

# Labels and messages follow the frontend (formalMessages.ts) so the PDF reads like the app.
DOCUMENT_TYPE_LABELS = {
    "unknown": "Nieznany",
    "lawsuit": "Pozew",
    "power_of_attorney": "Pełnomocnictwo",
    "application": "Wniosek",
    "invoice": "Faktura",
    "contract": "Umowa",
    "id_card": "Dowód osobisty",
    "passport": "Paszport",
    "bank_statement": "Wyciąg bankowy",
    "tax_form": "Formularz podatkowy",
    "financial_report": "Sprawozdanie finansowe",
    "government_tender": "Przetarg publiczny",
    "law_and_regulation": "Akt prawny",
    "manual": "Instrukcja",
    "patent": "Patent",
    "scientific_article": "Artykuł naukowy",
    "other": "Inny",
}
VALIDATION_STATUS_LABELS = {
    "complete": "Dokument kompletny",
    "incomplete": "Dokument niekompletny",
    "indeterminate": "Wymaga weryfikacji",
    "unsupported": "Rodzaj dokumentu nie jest jeszcze sprawdzany",
}
OUTCOME_LABELS = {
    "pass": "Spełnione",
    "fail": "Wymaga uwagi",
    "unknown": "Do weryfikacji",
    "not_applicable": "Niewymagane",
    "error": "Niedostępne",
}
OUTCOME_COLORS = {
    "pass": "#15803d",
    "fail": "#b91c1c",
    "unknown": "#b45309",
    "not_applicable": "#6b7280",
    "error": "#6b7280",
}
SEVERITY_LABELS = {"critical": "Krytyczny", "high": "Wysoki", "medium": "Średni"}
SEVERITY_COLORS = {"critical": "#b91c1c", "high": "#c2410c", "medium": "#b45309"}
FIELD_LABELS = {
    "has_date": "Data dokumentu",
    "has_principal_section": "Mocodawca",
    "has_attorney_section": "Pełnomocnik",
    "has_authorization_scope_section": "Zakres umocowania",
    "has_parties_section": "Oznaczenie stron",
    "has_subject_section": "Przedmiot umowy",
    "has_court_section": "Oznaczenie sądu",
    "has_claim_section": "Żądanie pozwu",
    "has_justification_section": "Uzasadnienie",
    "has_signature": "Podpis",
    "has_attachments_section": "Załączniki",
    "has_text": "Treść dokumentu",
    "document_type": "Rodzaj dokumentu",
}
REASON_MESSAGES = {
    "MASK_TYPE_MISMATCH": (
        "Ukryta wartość dotyczy innego rodzaju danych. Sprawdź, czy to pole jest wypełnione."
    ),
    "UNRECOGNIZED_NUMBER": "Nie udało się odczytać kwoty. Sprawdź ją w dokumencie.",
    "UNRECOGNIZED_IDENTIFIER": (
        "Nie udało się odczytać oznaczenia lub numeru. Sprawdź go w dokumencie."
    ),
    "FIELD_LAYOUT_UNCERTAIN": (
        "Nie można pewnie przypisać treści do tego pola. Sprawdź je w dokumencie."
    ),
    "VARIANT_FIELD_REVIEW": (
        "Sprawdź, czy ten element występuje i jest potrzebny w tym rodzaju dokumentu."
    ),
    "VISUAL_REVIEW_REQUIRED": "Sprawdź zdjęcie i zabezpieczenia na oryginale dokumentu.",
    "TABLE_CONTENT_UNVERIFIED": (
        "Sprawdź, czy tabela zawiera wypełnione pozycje, a nie tylko nagłówki."
    ),
    "EMPTY_FIELD": "To pole jest puste. Uzupełnij je w dokumencie.",
    "INVALID_DATE": "Ta data jest niepoprawna. Sprawdź dzień, miesiąc i rok.",
    "REDACTED_PRESENCE_CONFIRMED": (
        "Pole jest wypełnione. Jego treść została ukryta dla ochrony danych."
    ),
    "MASKED_CONTENT_UNVERIFIABLE": (
        "Pole jest wypełnione, ale jego treść jest ukryta. Sprawdź poprawność w oryginale."
    ),
    "REDACTED_VALUE": "Treść tego pola jest ukryta. Sprawdź ją w oryginale dokumentu.",
    "REDACTED_DATE": "Data jest ukryta. Jej poprawność możesz sprawdzić w oryginale dokumentu.",
    "REDACTED_PARTY": "Dane osoby są ukryte. Sprawdź je w oryginale dokumentu.",
    "PARTY_FIELD_EMPTY": "Dane co najmniej jednej ze stron są puste. Uzupełnij je w dokumencie.",
    "PARTY_NOT_FOUND": "Nie znaleziono danych obu stron. Sprawdź, czy dokument je zawiera.",
    "PARTIES_UNCERTAIN": "Sprawdź, czy dokument zawiera dane obu stron.",
    "SIGNATURE_REQUIRES_VISUAL_REVIEW": (
        "Sprawdź na oryginale dokumentu, czy znajduje się wymagany podpis."
    ),
    "LOW_OCR_CONFIDENCE": (
        "Ten fragment jest trudny do odczytania. Sprawdź go w oryginale dokumentu."
    ),
    "CONFLICTING_CANDIDATES": (
        "Znaleziono różne informacje dotyczące tego pola. Sprawdź, która jest właściwa."
    ),
    "CONFLICTING_DATES": "Znaleziono różne daty. Sprawdź, która jest datą dokumentu.",
    "UNRECOGNIZED_DATE": "Nie udało się odczytać daty. Sprawdź ją w dokumencie.",
    "INSUFFICIENT_VALUE": "Sprawdź, czy to pole zawiera wszystkie potrzebne informacje.",
    "SCOPE_NOT_ESTABLISHED": (
        "Sprawdź, czy opisano, do jakich czynności upoważniono pełnomocnika."
    ),
    "LAYOUT_UNAVAILABLE": (
        "Nie udało się dokładnie odczytać układu dokumentu. Sprawdź ten element ręcznie."
    ),
    "UNREADABLE_PAGE": (
        "Nie udało się odczytać przynajmniej jednej strony. Sprawdź czytelność dokumentu."
    ),
    "INSUFFICIENT_TEXT": (
        "Odczytano zbyt mało treści. Spróbuj przesłać wyraźniejszą kopię dokumentu."
    ),
    "NO_TEXT": "Nie udało się odczytać dokumentu. Spróbuj przesłać wyraźniejszą kopię.",
    "AZURE_OCR_REQUIRED": "Nie udało się odczytać dokumentu. Uruchom analizę ponownie.",
    "LEGACY_EXTRACTION": "Uruchom analizę ponownie, aby sprawdzić ten element.",
    "ATTACHMENT_CONTENT_UNVERIFIED": "Sprawdź, czy dołączono wszystkie wymienione załączniki.",
    "UNSUPPORTED_PROFILE": "Nie sprawdzamy jeszcze tego rodzaju dokumentów.",
    "NO_ATTACHMENT_REQUIREMENT": "Nie znaleziono informacji o wymaganych załącznikach.",
    "INVALID_RULE_CONFIG": "Nie udało się sprawdzić tego elementu. Spróbuj ponownie później.",
    "UNKNOWN_RULE_TYPE": "Nie udało się sprawdzić tego elementu. Spróbuj ponownie później.",
    "VALUE_UNAVAILABLE": "Nie udało się odczytać wartości tego pola. Sprawdź ją w dokumencie.",
    "PATTERN_MISMATCH": "Wartość ma nieprawidłowy format. Sprawdź ją w dokumencie.",
    "DATE_OUT_OF_RANGE": "Data znajduje się poza dozwolonym zakresem. Sprawdź ją w dokumencie.",
}
OUTCOME_MESSAGES = {
    "pass": "Ten element jest obecny i spełnia sprawdzane wymaganie.",
    "not_applicable": "Ten element nie jest wymagany w tym sprawdzeniu.",
    "error": "Nie udało się sprawdzić tego elementu. Spróbuj ponownie później.",
    "unknown": "Sprawdź ten element w oryginale dokumentu.",
    "fail": (
        "Brakuje wymaganych informacji lub nie spełniają one wymagania. "
        "Sprawdź ten element w dokumencie."
    ),
}

# Highlight colours match the document viewer in the app.
MARK_COLORS = {
    "fail": "#dc2626",
    "unknown": "#d97706",
    "error": "#6b7280",
    "critical": "#dc2626",
    "high": "#ea580c",
    "medium": "#ca8a04",
}
PREVIEW_LONG_SIDE_PX = 1400

CSS = """
* { font-family: sans-serif; }
body { font-size: 10px; color: #111827; }
h1 { font-size: 20px; margin: 0 0 4px 0; }
h2 { font-size: 13px; margin: 18px 0 6px 0; padding-bottom: 3px; border-bottom: 1px solid #d1d5db; }
p { margin: 0 0 6px 0; line-height: 1.4; }
.muted { color: #6b7280; }
.meta td { padding: 2px 12px 2px 0; vertical-align: top; }
.meta td.muted { width: 120px; }
table.items { width: 100%; }
.items td { padding: 5px 6px; vertical-align: top; border-top: 1px solid #e5e7eb; }
.items th { padding: 5px 6px; text-align: left; background-color: #f3f4f6; }
.items td.number { color: #6b7280; width: 20px; }
.label { font-weight: bold; }
.quote { color: #4b5563; font-style: italic; }
"""

PageImageLoader = Callable[[int], bytes | None]


@dataclass
class FormalCheck:
    label: str
    outcome: str
    message: str
    location: dict[str, Any] | None = None


@dataclass
class Mark:
    """A numbered place on a document page; rects are relative to the page (0-1)."""

    number: int
    page: int
    rects: list[dict[str, float]]
    color: str
    expected: bool = False


def _text(value: object) -> str:
    return escape(str(value)) if value not in (None, "") else ""


def _format_date(value: datetime | None) -> str:
    if value is None:
        return "-"
    if value.tzinfo is not None:
        value = value.astimezone(TIMEZONE)
    return value.strftime("%d.%m.%Y %H:%M")


def _document_type(document: Document, analysis: DocumentAnalysis) -> str:
    value = str(document.document_type or "unknown")
    if value == "unknown":
        value = analysis.detected_document_type or "unknown"
    return DOCUMENT_TYPE_LABELS.get(value, value)


def _check_label(rule_name: str | None, field_name: str | None) -> str:
    if field_name in FIELD_LABELS:
        return FIELD_LABELS[field_name]
    if rule_name == "Formal validation profile":
        return FIELD_LABELS["document_type"]
    return rule_name or "-"


def _check_message(outcome: str, reason_code: str | None, message: str | None) -> str:
    # Rule results use the same wording as the app; plain issues keep the backend message.
    if reason_code and reason_code in REASON_MESSAGES:
        return REASON_MESSAGES[reason_code]
    return message or OUTCOME_MESSAGES.get(outcome, OUTCOME_MESSAGES["fail"])


def _issue_outcome(issue: dict[str, Any]) -> str:
    details = issue.get("details") or {}
    outcome = details.get("outcome")
    if outcome in OUTCOME_LABELS:
        return str(outcome)
    return "fail" if issue.get("severity") == "error" else "unknown"


def formal_checks(report: AnalysisReport | None) -> list[FormalCheck]:
    """Checks to list in the PDF: rule results first, then issues no rule result covers."""
    if report is None:
        return []
    summary = report.summary or {}
    issues = [*(report.errors or []), *(report.warnings or [])]
    # Issues of one rule are matched to its results in order, so each result gets its own place
    issues_by_rule: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        rule_id = (issue.get("details") or {}).get("rule_id")
        if rule_id:
            issues_by_rule.setdefault(rule_id, []).append(issue)
    checks: list[FormalCheck] = []
    covered: set[str] = set()

    rule_results = summary.get("rule_results")
    if summary.get("schema_version") == RULE_RESULTS_SCHEMA_VERSION and isinstance(
        rule_results, list
    ):
        for result in rule_results:
            outcome = result.get("outcome", "unknown")
            rule_id = result.get("rule_id") or ""
            covered.add(rule_id)
            matched = issues_by_rule.get(rule_id) or []
            issue = (
                matched.pop(0) if matched and outcome not in {"pass", "not_applicable"} else None
            )
            checks.append(
                FormalCheck(
                    label=_check_label(result.get("rule_name"), result.get("field_name")),
                    outcome=outcome,
                    message=_check_message(outcome, result.get("reason_code"), None),
                    location=((issue or {}).get("details") or {}).get("location"),
                )
            )

    for issue in issues:
        details = issue.get("details") or {}
        if details.get("rule_id") and details["rule_id"] in covered:
            continue
        outcome = _issue_outcome(issue)
        checks.append(
            FormalCheck(
                label=_check_label(issue.get("rule_name"), issue.get("field_name")),
                outcome=outcome,
                message=_check_message(outcome, details.get("reason_code"), issue.get("message")),
                location=details.get("location"),
            )
        )
    return checks


def _needs_attention(check: FormalCheck) -> bool:
    return check.outcome not in {"pass", "not_applicable"}


def _legal_errors(analysis: DocumentAnalysis) -> list[dict[str, Any]]:
    result = analysis.legal_analysis_result or {}
    return [error for error in result.get("errors") or [] if isinstance(error, dict)]


def _page_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def collect_marks(checks: list[FormalCheck], errors: list[dict[str, Any]]) -> list[list[Mark]]:
    """Numbered places of formal checks needing attention, then of legal analysis errors.

    Returns marks per item in table order; an item without a known page has no marks.
    """
    marks: list[list[Mark]] = []
    number = 0
    for check in filter(_needs_attention, checks):
        number += 1
        location = check.location or {}
        page = _page_number(location.get("page"))
        color = MARK_COLORS.get(check.outcome, MARK_COLORS["fail"])
        marks.append(
            [
                Mark(
                    number,
                    page,
                    location.get("rects") or [],
                    color,
                    expected=location.get("kind") == "expected",
                )
            ]
            if page
            else []
        )
    for error in errors:
        number += 1
        color = MARK_COLORS.get(str(error.get("severity")), MARK_COLORS["medium"])
        locations = [loc for loc in error.get("locations") or [] if _page_number(loc.get("page"))]
        if locations:
            marks.append(
                [Mark(number, loc["page"], loc.get("rects") or [], color) for loc in locations]
            )
        elif page := _page_number(error.get("page")):
            marks.append([Mark(number, page, [], color)])
        else:
            marks.append([])
    return marks


def _formal_section(report: AnalysisReport | None, checks: list[FormalCheck]) -> str:
    parts = ["<h2>Kompletność formalna</h2>"]
    if report is None:
        parts.append('<p class="muted">Walidacja formalna nie jest dostępna dla tej analizy.</p>')
        return "".join(parts)

    summary = report.summary or {}
    status = summary.get("validation_status")
    if status is None and report.is_complete is not None:
        status = "complete" if report.is_complete else "incomplete"
    if status in VALIDATION_STATUS_LABELS:
        parts.append(f'<p><span class="label">Wynik:</span> {VALIDATION_STATUS_LABELS[status]}')
        if report.completeness_score is not None:
            parts.append(f" ({round(report.completeness_score * 100)}% wymaganych elementów)")
        parts.append("</p>")

    if not checks:
        parts.append('<p class="muted">Nie wykryto braków formalnych.</p>')
        return "".join(parts)

    rows = []
    number = 0
    for check in checks:
        if _needs_attention(check):
            number += 1
        color = OUTCOME_COLORS.get(check.outcome, "#111827")
        label = OUTCOME_LABELS.get(check.outcome, _text(check.outcome))
        rows.append(
            f'<tr><td class="number">{number if _needs_attention(check) else ""}</td>'
            f'<td class="label">{_text(check.label)}</td>'
            f'<td style="color: {color}">{label}</td>'
            f"<td>{_text(check.message)}</td></tr>"
        )
    parts.append(
        '<table class="items">'
        "<tr><th>Nr</th><th>Element</th><th>Wynik</th><th>Opis</th></tr>"
        f"{''.join(rows)}</table>"
    )
    return "".join(parts)


def _legal_section(analysis: DocumentAnalysis, first_number: int) -> str:
    result = analysis.legal_analysis_result or {}
    parts = ["<h2>Analiza prawna</h2>"]
    if result.get("summary"):
        parts.append(f'<p><span class="label">Podsumowanie:</span> {_text(result["summary"])}</p>')

    errors = _legal_errors(analysis)
    if errors:
        rows = []
        for number, error in enumerate(errors, start=first_number):
            severity = str(error.get("severity", "medium"))
            quote = (
                f'<p class="quote">„{_text(error["text_reference"])}”</p>'
                if error.get("text_reference")
                else ""
            )
            color = SEVERITY_COLORS.get(severity, "#111827")
            label = SEVERITY_LABELS.get(severity, _text(severity))
            page = _text(error.get("page")) or "-"
            rows.append(
                f'<tr><td class="number">{number}</td>'
                f'<td style="color: {color}">{label}</td><td>{page}</td>'
                f"<td><p>{_text(error.get('issue'))}</p>{quote}</td></tr>"
            )
        parts.append(
            '<table class="items">'
            "<tr><th>Nr</th><th>Istotność</th><th>Strona</th><th>Nieprawidłowość</th></tr>"
            f"{''.join(rows)}</table>"
        )
    else:
        parts.append('<p class="muted">Nie wykryto nieprawidłowości.</p>')

    laws = [law for law in result.get("applicable_laws") or [] if isinstance(law, dict)]
    if laws:
        parts.append("<h2>Przepisy prawne</h2>")
        for law in laws:
            reference = (
                f' <span class="muted">({_text(law["reference"])})</span>'
                if law.get("reference")
                else ""
            )
            name = _text(law.get("law"))
            parts.append(
                f'<p><span class="label">{name}</span>{reference}<br/>'
                f"{_text(law.get('description'))}</p>"
            )
    return "".join(parts)


def _report_html(
    document: Document,
    analysis: DocumentAnalysis,
    report: AnalysisReport | None,
    checks: list[FormalCheck],
    with_preview: bool,
) -> str:
    meta = [
        ("Dokument", _text(document.original_filename)),
        ("Rodzaj dokumentu", _text(_document_type(document, analysis))),
        ("Data analizy", _format_date(analysis.updated_at or analysis.created_at)),
        ("Identyfikator analizy", _text(analysis.id)),
    ]
    meta_rows = "".join(
        f'<tr><td class="muted">{label}</td><td>{value}</td></tr>' for label, value in meta
    )
    preview_note = (
        '<p class="muted">Numery z kolumny „Nr” wskazują miejsca zaznaczone '
        "na podglądzie stron dokumentu na końcu raportu.</p>"
        if with_preview
        else ""
    )
    first_legal_number = sum(map(_needs_attention, checks)) + 1
    return (
        "<h1>Raport z analizy dokumentu</h1>"
        f'<p class="muted">Wygenerowano {_format_date(datetime.now(TIMEZONE))}</p>'
        f'<table class="meta">{meta_rows}</table>'
        f"{preview_note}"
        f"{_formal_section(report, checks)}"
        f"{_legal_section(analysis, first_legal_number)}"
    )


def _rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return (int(value[0:2], 16) / 255, int(value[2:4], 16) / 255, int(value[4:6], 16) / 255)


def _fit(image_size: tuple[int, int], box: fitz.Rect) -> fitz.Rect:
    width, height = image_size
    scale = min(box.width / width, box.height / height)
    left = box.x0 + (box.width - width * scale) / 2
    return fitz.Rect(left, box.y0, left + width * scale, box.y0 + height * scale)


def _draw_mark(page: fitz.Page, image_rect: fitz.Rect, mark: Mark, labels: list[fitz.Rect]) -> None:
    color = _rgb(mark.color)
    for index, rect in enumerate(mark.rects):
        box = fitz.Rect(
            image_rect.x0 + rect["x"] * image_rect.width,
            image_rect.y0 + rect["y"] * image_rect.height,
            image_rect.x0 + (rect["x"] + rect["width"]) * image_rect.width,
            image_rect.y0 + (rect["y"] + rect["height"]) * image_rect.height,
        )
        page.draw_rect(
            box,
            color=color,
            fill=color,
            fill_opacity=0.15,
            width=1.2,
            dashes="[3 2] 0" if mark.expected else None,
        )
        if index == 0:
            _draw_label(page, image_rect, box, mark, labels)


def _draw_label(
    page: fitz.Page, image_rect: fitz.Rect, box: fitz.Rect, mark: Mark, labels: list[fitz.Rect]
) -> None:
    """Number in the left margin level with the box, so it never covers the document text."""
    width = 8 + 5 * len(str(mark.number))
    right = image_rect.x0 - 4
    label = fitz.Rect(right - width, box.y0, right, box.y0 + 11)
    while any(label.intersects(other) for other in labels):
        label = fitz.Rect(label.x0 - width - 2, label.y0, label.x1 - width - 2, label.y1)
    labels.append(label)
    color = _rgb(mark.color)
    page.draw_rect(label, color=color, fill=color, width=0)
    page.insert_text((label.x0 + 4, label.y1 - 3), str(mark.number), fontsize=8, color=(1, 1, 1))


def _add_page_previews(
    doc: fitz.Document, marks: list[Mark], page_image: PageImageLoader, redacted: bool
) -> None:
    pages = sorted({mark.page for mark in marks})
    for page_number in pages:
        image = page_image(page_number)
        if image is None:
            continue
        pixmap = fitz.Pixmap(image)
        image_size = (pixmap.width, pixmap.height)
        page = doc.new_page(width=fitz.paper_rect("a4").width, height=fitz.paper_rect("a4").height)
        on_page = [mark for mark in marks if mark.page == page_number]
        unplaced = sorted({str(mark.number) for mark in on_page if not mark.rects}, key=int)

        subtitle = "Podgląd z zamaskowanymi danymi wrażliwymi. " if redacted else ""
        if unplaced:
            subtitle += f"Na tej stronie, bez dokładnego położenia: {', '.join(unplaced)}."
        header = fitz.Rect(
            PAGE_MARGIN, PAGE_MARGIN, page.rect.width - PAGE_MARGIN, PAGE_MARGIN + 44
        )
        page.insert_htmlbox(
            header,
            f'<h2 style="margin-top: 0">Strona {page_number} dokumentu</h2>'
            f'<p class="muted">{_text(subtitle)}</p>',
            css=CSS,
        )
        box = fitz.Rect(
            PAGE_MARGIN,
            header.y1 + 6,
            page.rect.width - PAGE_MARGIN,
            page.rect.height - PAGE_MARGIN,
        )
        image_rect = _fit(image_size, box)
        page.insert_image(image_rect, stream=image)
        page.draw_rect(image_rect, color=(0.82, 0.84, 0.86), width=0.5)
        labels: list[fitz.Rect] = []
        for mark in on_page:
            _draw_mark(page, image_rect, mark, labels)


def _add_page_numbers(doc: fitz.Document) -> None:
    for page in doc:
        page.insert_text(
            (PAGE_MARGIN, page.rect.height - PAGE_MARGIN / 2),
            f"Strona {page.number + 1} z {doc.page_count}",
            fontsize=8,
            color=(0.42, 0.45, 0.5),
        )


def render_report_pdf(
    document: Document,
    analysis: DocumentAnalysis,
    report: AnalysisReport | None,
    page_image: PageImageLoader | None = None,
    redacted_preview: bool = True,
) -> bytes:
    """Render the report; with `page_image`, pages with marked issues are appended as previews."""
    checks = formal_checks(report)
    marks = [mark for item in collect_marks(checks, _legal_errors(analysis)) for mark in item]
    with_preview = page_image is not None and bool(marks)

    html = _report_html(document, analysis, report, checks, with_preview)
    story = fitz.Story(html=html, user_css=CSS)
    buffer = io.BytesIO()
    writer = fitz.DocumentWriter(buffer)
    page_rect = fitz.paper_rect("a4")
    content_rect = fitz.Rect(
        PAGE_MARGIN, PAGE_MARGIN, page_rect.width - PAGE_MARGIN, page_rect.height - PAGE_MARGIN
    )
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(device)
        writer.end_page()
    writer.close()

    doc = fitz.open(stream=buffer.getvalue(), filetype="pdf")
    if with_preview and page_image is not None:
        _add_page_previews(doc, marks, page_image, redacted_preview)
    _add_page_numbers(doc)
    return bytes(doc.tobytes(garbage=3, deflate=True))


def preview_loader(document: Document, analysis: DocumentAnalysis) -> tuple[PageImageLoader, bool]:
    """Page images for the report, redacted when available; returns the loader and that flag."""
    previews = PagePreviewService(Path(document.storage_path), document.mime_type, str(analysis.id))
    variants = previews.available_variants()
    variant: PreviewVariant = "redacted" if "redacted" in variants else "original"

    def load(page: int) -> bytes | None:
        # A missing preview only drops that page from the report, the export itself goes on
        try:
            path = previews.get_preview(variant, page, "lg")
            with Image.open(path) as image:
                image.thumbnail((PREVIEW_LONG_SIDE_PX, PREVIEW_LONG_SIDE_PX))
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=80)
        except (NotFoundError, RuntimeError, OSError, ValueError):
            logger.warning(
                "Report page preview unavailable", analysis_id=str(analysis.id), page=page
            )
            return None
        return buffer.getvalue()

    return load, variant == "redacted"


def report_filename(original_filename: str) -> str:
    stem = Path(original_filename).stem.strip() or "dokument"
    return f"raport_{stem}.pdf"


def content_disposition(filename: str) -> str:
    # ASCII fallback for old clients; filename* keeps Polish characters.
    ascii_name = unicodedata.normalize("NFKD", filename.replace("ł", "l").replace("Ł", "L"))
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name.encode("ascii", "ignore").decode())
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
