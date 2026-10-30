"""Tests for multi-page handling in the legal analysis task: page markers and bbox mapping."""

from __future__ import annotations

from typing import Any

from app.adapters.azure_ocr import OCRPage, OCRResult
from app.tasks.legal_analysis import _build_paged_text, _coerce_page, _map_error_to_bbox


def _words(page: int, text: str, start_id: int = 0, y: int = 10) -> list[dict[str, Any]]:
    return [
        {
            "id": f"w{start_id + i}",
            "text": token,
            "bbox": {"x": 10 + 50 * i, "y": y, "width": 40, "height": 12},
            "page": page,
        }
        for i, token in enumerate(text.split())
    ]


def _ocr_result(text: str, pages: list[OCRPage]) -> OCRResult:
    return OCRResult(raw={}, text_content=text, pages=pages, page_count=len(pages), confidence=None)


def test_paged_text_marks_every_page() -> None:
    ocr_result = _ocr_result(
        "ignored",
        [
            OCRPage(page_number=1, width=1, height=1, lines=["Pozew o rozwód"]),
            OCRPage(page_number=2, width=1, height=1, lines=["Uzasadnienie", "Podpis"]),
        ],
    )

    assert _build_paged_text(ocr_result) == (
        "[Strona 1]\nPozew o rozwód\n\n[Strona 2]\nUzasadnienie\nPodpis"
    )


def test_paged_text_falls_back_to_content_without_pages() -> None:
    assert _build_paged_text(_ocr_result("abc", [])) == "abc"


def test_maps_multi_word_reference_to_union_bbox() -> None:
    words = _words(1, "Sąd Okręgowy w Warszawie wydział cywilny")

    match = _map_error_to_bbox("Sąd Okręgowy w Warszawie", words)

    assert match == {
        "id": "w0",
        "bbox": {"x": 10, "y": 10, "width": 190, "height": 12},
        "page": 1,
    }


def test_prefers_page_suggested_by_llm() -> None:
    words = _words(1, "własnoręczny podpis powoda") + _words(3, "własnoręczny podpis powoda", 10)

    match = _map_error_to_bbox("(własnoręczny podpis powoda)", words, page=3)

    assert match is not None
    assert match["page"] == 3
    assert match["id"] == "w10"


def test_finds_reference_on_other_page_when_llm_page_is_wrong() -> None:
    words = _words(1, "Pozew o rozwód") + _words(4, "Uzasadnienie opisać sytuację", 10)

    match = _map_error_to_bbox("Uzasadnienie opisać sytuację", words, page=1)

    assert match is not None
    assert match["page"] == 4


def test_returns_none_for_unmatched_or_missing_reference() -> None:
    words = _words(1, "Pozew o rozwód")

    assert _map_error_to_bbox("zupełnie inny tekst umowy", words) is None
    assert _map_error_to_bbox("brak", words) is None
    assert _map_error_to_bbox("...", words) is None


def test_coerce_page() -> None:
    assert _coerce_page(2) == 2
    assert _coerce_page(" 3 ") == 3
    assert _coerce_page(None) is None
    assert _coerce_page("druga") is None
    assert _coerce_page(True) is None


def _normalized_words(page: int, lines: list[str]) -> list[dict[str, Any]]:
    """Words laid out in lines, with normalized boxes and Tesseract line keys."""
    words: list[dict[str, Any]] = []
    for line_no, line in enumerate(lines):
        for i, token in enumerate(line.split()):
            box = {"x": 0.1 + 0.1 * i, "y": 0.1 + 0.05 * line_no, "width": 0.08, "height": 0.02}
            words.append(
                {
                    "id": f"w{len(words)}",
                    "text": token,
                    "bbox": {k: v * 1000 for k, v in box.items()},
                    "nbbox": box,
                    "line": f"1-1-{line_no}",
                    "page": page,
                }
            )
    return words


def test_returns_one_normalized_rect_per_line() -> None:
    words = _normalized_words(2, ["Sąd Okręgowy w", "Krakowie wydział cywilny"])

    match = _map_error_to_bbox("Sąd Okręgowy w Krakowie", words)

    assert match is not None
    assert match["page"] == 2
    assert match["rects"] == [
        {"x": 0.1, "y": 0.1, "width": 0.28, "height": 0.02},
        {"x": 0.1, "y": 0.15, "width": 0.08, "height": 0.02},
    ]


def test_no_rects_for_words_without_normalized_boxes() -> None:
    match = _map_error_to_bbox("Pozew o rozwód", _words(1, "Pozew o rozwód"))

    assert match is not None
    assert "rects" not in match


def test_azure_words_provide_normalized_multiline_highlights() -> None:
    from app.tasks.legal_analysis import _extract_azure_words

    raw = {
        "pages": [
            {
                "pageNumber": 3,
                "unit": "pixel",
                "width": 1000,
                "height": 2000,
                "lines": [
                    {"spans": [{"offset": 0, "length": 12}]},
                    {"spans": [{"offset": 13, "length": 10}]},
                ],
                "words": [
                    {
                        "content": text,
                        "span": {"offset": offset, "length": len(text)},
                        "polygon": [x, y, x + 80, y, x + 80, y + 40, x, y + 40],
                    }
                    for text, offset, x, y in [
                        ("Sąd", 0, 100, 200),
                        ("Okręgowy", 4, 200, 200),
                        ("w", 13, 100, 300),
                        ("Krakowie", 15, 200, 300),
                    ]
                ],
            }
        ]
    }

    match = _map_error_to_bbox("Sąd Okręgowy w Krakowie", _extract_azure_words(raw), page=3)

    assert match is not None
    assert match["page"] == 3
    assert match["rects"] == [
        {"x": 0.1, "y": 0.1, "width": 0.18, "height": 0.02},
        {"x": 0.1, "y": 0.15, "width": 0.18, "height": 0.02},
    ]
