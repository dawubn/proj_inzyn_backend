"""PDF export of a finished analysis: formal validation and legal analysis in one document."""

from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import fitz

from app.models.analysis_report import AnalysisReport
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis

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
.items td { padding: 5px 6px; vertical-align: top; border-bottom: 1px solid #e5e7eb; }
.items th { padding: 5px 6px; text-align: left; background-color: #f3f4f6; }
.label { font-weight: bold; }
.quote { color: #4b5563; font-style: italic; }
"""


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


def formal_checks(report: AnalysisReport | None) -> list[dict[str, str]]:
    """Checks to list in the PDF: rule results first, then issues no rule result covers."""
    if report is None:
        return []
    summary = report.summary or {}
    issues = [*(report.errors or []), *(report.warnings or [])]
    checks: list[dict[str, str]] = []
    covered: set[str] = set()

    rule_results = summary.get("rule_results")
    if summary.get("schema_version") == RULE_RESULTS_SCHEMA_VERSION and isinstance(
        rule_results, list
    ):
        for result in rule_results:
            outcome = result.get("outcome", "unknown")
            covered.add(result.get("rule_id") or "")
            checks.append(
                {
                    "label": _check_label(result.get("rule_name"), result.get("field_name")),
                    "outcome": outcome,
                    "message": _check_message(outcome, result.get("reason_code"), None),
                }
            )

    for issue in issues:
        details = issue.get("details") or {}
        if details.get("rule_id") and details["rule_id"] in covered:
            continue
        outcome = _issue_outcome(issue)
        checks.append(
            {
                "label": _check_label(issue.get("rule_name"), issue.get("field_name")),
                "outcome": outcome,
                "message": _check_message(
                    outcome, details.get("reason_code"), issue.get("message")
                ),
            }
        )
    return checks


def _formal_section(report: AnalysisReport | None) -> str:
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

    checks = formal_checks(report)
    if not checks:
        parts.append('<p class="muted">Nie wykryto braków formalnych.</p>')
        return "".join(parts)

    rows = []
    for check in checks:
        outcome = check["outcome"]
        color = OUTCOME_COLORS.get(outcome, "#111827")
        label = OUTCOME_LABELS.get(outcome, _text(outcome))
        rows.append(
            f'<tr><td class="label">{_text(check["label"])}</td>'
            f'<td style="color: {color}">{label}</td>'
            f"<td>{_text(check['message'])}</td></tr>"
        )
    parts.append(
        '<table class="items">'
        "<tr><th>Element</th><th>Wynik</th><th>Opis</th></tr>"
        f"{''.join(rows)}</table>"
    )
    return "".join(parts)


def _legal_section(result: dict[str, Any] | None) -> str:
    result = result or {}
    parts = ["<h2>Analiza prawna</h2>"]
    if result.get("summary"):
        parts.append(f'<p><span class="label">Podsumowanie:</span> {_text(result["summary"])}</p>')

    errors = [error for error in result.get("errors") or [] if isinstance(error, dict)]
    if errors:
        rows = []
        for error in errors:
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
                f'<tr><td style="color: {color}">{label}</td><td>{page}</td>'
                f"<td><p>{_text(error.get('issue'))}</p>{quote}</td></tr>"
            )
        parts.append(
            '<table class="items">'
            "<tr><th>Istotność</th><th>Strona</th><th>Nieprawidłowość</th></tr>"
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
    document: Document, analysis: DocumentAnalysis, report: AnalysisReport | None
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
    return (
        "<h1>Raport z analizy dokumentu</h1>"
        f'<p class="muted">Wygenerowano {_format_date(datetime.now(TIMEZONE))}</p>'
        f'<table class="meta">{meta_rows}</table>'
        f"{_formal_section(report)}"
        f"{_legal_section(analysis.legal_analysis_result)}"
    )


def _add_page_numbers(pdf: bytes) -> bytes:
    doc = fitz.open(stream=pdf, filetype="pdf")
    for page in doc:
        page.insert_text(
            (PAGE_MARGIN, page.rect.height - PAGE_MARGIN / 2),
            f"Strona {page.number + 1} z {doc.page_count}",
            fontsize=8,
            color=(0.42, 0.45, 0.5),
        )
    return bytes(doc.tobytes(garbage=3, deflate=True))


def render_report_pdf(
    document: Document, analysis: DocumentAnalysis, report: AnalysisReport | None
) -> bytes:
    story = fitz.Story(html=_report_html(document, analysis, report), user_css=CSS)
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
    return _add_page_numbers(buffer.getvalue())


def report_filename(original_filename: str) -> str:
    stem = Path(original_filename).stem.strip() or "dokument"
    return f"raport_{stem}.pdf"


def content_disposition(filename: str) -> str:
    # ASCII fallback for old clients; filename* keeps Polish characters.
    ascii_name = unicodedata.normalize("NFKD", filename.replace("ł", "l").replace("Ł", "L"))
    fallback = re.sub(r"[^A-Za-z0-9._-]+", "_", ascii_name.encode("ascii", "ignore").decode())
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"
