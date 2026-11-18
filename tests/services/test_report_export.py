import uuid
from datetime import UTC, datetime
from typing import Any

import fitz

from app.models.analysis_report import AnalysisReport
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.services.report_export import (
    FormalCheck,
    Mark,
    collect_marks,
    content_disposition,
    formal_checks,
    render_report_pdf,
    report_filename,
)


def _document(filename: str = "pełnomocnictwo.pdf") -> Document:
    return Document(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        original_filename=filename,
        storage_path="storage/doc.pdf",
        file_extension="pdf",
        file_size_bytes=100,
        mime_type="application/pdf",
        document_type="power_of_attorney",
    )


def _analysis(legal: dict[str, Any] | None = None) -> DocumentAnalysis:
    return DocumentAnalysis(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        status="completed",
        created_at=datetime(2026, 11, 3, 9, 30, tzinfo=UTC),
        updated_at=datetime(2026, 11, 3, 9, 31, tzinfo=UTC),
        legal_analysis_result=legal,
    )


def _v2_report() -> AnalysisReport:
    return AnalysisReport(
        analysis_id=uuid.uuid4(),
        status="completed",
        errors=[
            {
                "rule_name": "Podpis",
                "field_name": "has_signature",
                "severity": "error",
                "message": "Podpis: brak",
                "details": {
                    "rule_id": "signature",
                    "outcome": "fail",
                    "reason_code": "EMPTY_FIELD",
                },
            }
        ],
        warnings=[
            {
                "rule_name": "Formal validation profile",
                "field_name": "document_type",
                "severity": "warning",
                "message": "Brak aktywnego profilu walidacji.",
                "details": {"outcome": "unknown", "reason_code": "UNSUPPORTED_PROFILE"},
            }
        ],
        infos=[],
        error_count=1,
        warning_count=1,
        is_complete=False,
        completeness_score=0.5,
        summary={
            "schema_version": 2,
            "validation_status": "incomplete",
            "rule_results": [
                {
                    "rule_id": "date",
                    "rule_name": "Data",
                    "field_name": "has_date",
                    "outcome": "pass",
                    "severity": "error",
                    "reason_code": "FOUND",
                    "message": "Data: obecna",
                },
                {
                    "rule_id": "signature",
                    "rule_name": "Podpis",
                    "field_name": "has_signature",
                    "outcome": "fail",
                    "severity": "error",
                    "reason_code": "EMPTY_FIELD",
                    "message": "Podpis: brak",
                },
            ],
        },
    )


def _pdf_text(pdf: bytes) -> str:
    doc = fitz.open(stream=pdf, filetype="pdf")
    return " ".join(" ".join(page.get_text().split()) for page in doc)


def test_formal_checks_include_issues_without_rule_result() -> None:
    checks = formal_checks(_v2_report())

    assert [check.label for check in checks] == ["Data dokumentu", "Podpis", "Rodzaj dokumentu"]
    assert checks[1].message == "To pole jest puste. Uzupełnij je w dokumencie."
    assert checks[2].outcome == "unknown"


def test_formal_checks_keep_backend_message_for_unknown_reason() -> None:
    report = AnalysisReport(
        analysis_id=uuid.uuid4(),
        errors=[
            {
                "rule_name": "Podpis mocodawcy",
                "field_name": None,
                "severity": "error",
                "message": "Brak podpisu mocodawcy",
                "details": {},
            }
        ],
        warnings=[],
        summary={"total_rules": 1, "issues": 1},
    )

    checks = formal_checks(report)

    assert checks == [FormalCheck("Podpis mocodawcy", "fail", "Brak podpisu mocodawcy")]


def test_formal_checks_match_locations_of_repeated_rule_in_order() -> None:
    report = _v2_report()
    first = {
        "kind": "placeholder",
        "page": 1,
        "rects": [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.02}],
    }
    second = {"kind": "expected", "page": 2, "rects": []}
    report.errors = [
        {**report.errors[0], "details": {**report.errors[0]["details"], "location": first}},
        {**report.errors[0], "details": {**report.errors[0]["details"], "location": second}},
    ]
    summary = report.summary or {}
    summary["rule_results"].append(summary["rule_results"][1])

    checks = formal_checks(report)

    assert [check.location for check in checks if check.label == "Podpis"] == [first, second]


def test_collect_marks_numbers_formal_checks_then_legal_errors() -> None:
    rect = {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02}
    checks = [
        FormalCheck("Data dokumentu", "pass", "ok"),
        FormalCheck("Podpis", "fail", "brak", {"kind": "expected", "page": 2, "rects": [rect]}),
        FormalCheck("Rodzaj dokumentu", "unknown", "?"),
    ]
    errors: list[dict[str, Any]] = [
        {"issue": "a", "severity": "high", "page": 1, "locations": [{"page": 1, "rects": [rect]}]},
        {"issue": "b", "severity": "medium", "page": 3},
        {"issue": "c", "severity": "critical"},
    ]

    marks = collect_marks(checks, errors)

    assert marks == [
        [Mark(1, 2, [rect], "#dc2626", expected=True)],
        [],
        [Mark(3, 1, [rect], "#ea580c")],
        [Mark(4, 3, [], "#ca8a04")],
        [],
    ]


def test_render_report_pdf_contains_formal_and_legal_sections() -> None:
    legal = {
        "summary": "Pełnomocnictwo procesowe do reprezentowania przed sądem.",
        "errors": [
            {
                "issue": "Brak daty udzielenia pełnomocnictwa",
                "text_reference": "Udzielam pełnomocnictwa",
                "severity": "high",
                "page": 1,
            }
        ],
        "applicable_laws": [
            {"law": "Kodeks postępowania cywilnego", "reference": "art. 89", "description": "Forma"}
        ],
        "prompt": "nie powinno trafić do raportu",
    }

    pdf = render_report_pdf(_document(), _analysis(legal), _v2_report())
    text = _pdf_text(pdf)

    assert pdf.startswith(b"%PDF")
    assert "pełnomocnictwo.pdf" in text
    assert "Pełnomocnictwo" in text
    assert "Dokument niekompletny (50% wymaganych elementów)" in text
    assert "Wymaga uwagi" in text
    assert "Brak daty udzielenia pełnomocnictwa" in text
    assert "Kodeks postępowania cywilnego" in text
    assert "Strona 1 z" in text
    assert "nie powinno trafić" not in text


def test_render_report_pdf_without_formal_report() -> None:
    text = _pdf_text(render_report_pdf(_document(), _analysis(), None))

    assert "Walidacja formalna nie jest dostępna" in text
    assert "Nie wykryto nieprawidłowości." in text


def test_render_report_pdf_spans_multiple_pages() -> None:
    legal = {
        "errors": [
            {"issue": f"Nieprawidłowość {index}", "severity": "medium", "page": 1}
            for index in range(120)
        ]
    }

    doc = fitz.open(stream=render_report_pdf(_document(), _analysis(legal), None), filetype="pdf")

    assert doc.page_count > 1
    assert f"Strona {doc.page_count} z {doc.page_count}" in doc[-1].get_text()


def _page_png() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.insert_text((40, 60), "Udzielam pelnomocnictwa")
    return bytes(page.get_pixmap().tobytes("png"))


def test_render_report_pdf_appends_marked_pages() -> None:
    legal = {
        "errors": [
            {
                "issue": "Zbyt ogólny zakres",
                "severity": "high",
                "page": 1,
                "locations": [
                    {"page": 1, "rects": [{"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.05}]}
                ],
            },
            {"issue": "Brak wskazania sądu", "severity": "medium", "page": 1},
            {"issue": "Strona bez podglądu", "severity": "medium", "page": 5},
        ]
    }
    requested: list[int] = []

    def page_image(page: int) -> bytes | None:
        requested.append(page)
        return _page_png() if page == 1 else None

    doc = fitz.open(
        stream=render_report_pdf(_document(), _analysis(legal), None, page_image),
        filetype="pdf",
    )
    preview = " ".join(doc[-1].get_text().split())

    assert requested == [1, 5]
    assert doc.page_count == 2
    assert "Numery z kolumny „Nr”" in doc[0].get_text()
    assert "Strona 1 dokumentu" in preview
    assert "Podgląd z zamaskowanymi danymi wrażliwymi." in preview
    assert "bez dokładnego położenia: 2." in preview
    assert len(doc[-1].get_images()) == 1
    assert any(drawing["fill"] for drawing in doc[-1].get_drawings())


def test_render_report_pdf_skips_previews_without_marks() -> None:
    doc = fitz.open(
        stream=render_report_pdf(_document(), _analysis(), None, lambda _page: _page_png()),
        filetype="pdf",
    )

    assert doc.page_count == 1
    assert "Numery z kolumny" not in doc[0].get_text()


def test_content_disposition_keeps_polish_filename() -> None:
    filename = report_filename("Umowa najmu łódź.pdf")

    assert filename == "raport_Umowa najmu łódź.pdf"
    assert content_disposition(filename) == (
        'attachment; filename="raport_Umowa_najmu_lodz.pdf"; '
        "filename*=UTF-8''raport_Umowa%20najmu%20%C5%82%C3%B3d%C5%BA.pdf"
    )
