"""Tests for page previews rendered for the analysis viewer."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest
from PIL import Image

from app.core.exceptions import NotFoundError
from app.services.page_preview import PagePreviewService, previews_dir, redacted_path

ANALYSIS_ID = "00000000-0000-0000-0000-0000000000aa"


def _make_pdf(path: Path, pages: int) -> Path:
    with fitz.open() as doc:
        for i in range(pages):
            doc.new_page(width=595, height=842).insert_text((72, 72), f"Strona {i + 1}")
        doc.save(str(path))
    return path


def _make_redacted_tiff(original: Path, pages: int) -> Path:
    frames = [Image.new("RGB", (1240, 1754), "white") for _ in range(pages)]
    path = redacted_path(original, ANALYSIS_ID)
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    return path


def test_page_sizes_prefer_redacted_tiff(tmp_path: Path) -> None:
    original = _make_pdf(tmp_path / "doc.pdf", 2)
    _make_redacted_tiff(original, 2)

    sizes = PagePreviewService(original, "application/pdf", ANALYSIS_ID).page_sizes()

    assert [(s.page, s.width, s.height) for s in sizes] == [(1, 1240, 1754), (2, 1240, 1754)]


def test_page_sizes_fall_back_to_original_pdf(tmp_path: Path) -> None:
    original = _make_pdf(tmp_path / "doc.pdf", 3)
    service = PagePreviewService(original, "application/pdf", ANALYSIS_ID)

    assert [s.page for s in service.page_sizes()] == [1, 2, 3]
    assert service.available_variants() == ["original"]


def test_renders_and_caches_both_sizes(tmp_path: Path) -> None:
    original = _make_pdf(tmp_path / "doc.pdf", 2)
    _make_redacted_tiff(original, 2)
    service = PagePreviewService(original, "application/pdf", ANALYSIS_ID)

    lg = service.get_preview("original", 2, "lg")
    sm = service.get_preview("original", 2, "sm")

    assert Image.open(lg).format == "WEBP"
    assert max(Image.open(lg).size) == 2000
    assert max(Image.open(sm).size) == 1000
    assert service.get_preview("original", 2, "lg") == lg


def test_redacted_preview_keeps_page_aspect_ratio(tmp_path: Path) -> None:
    original = _make_pdf(tmp_path / "doc.pdf", 1)
    _make_redacted_tiff(original, 1)
    service = PagePreviewService(original, "application/pdf", ANALYSIS_ID)

    width, height = Image.open(service.get_preview("redacted", 1, "sm")).size

    assert abs(width / height - 1240 / 1754) < 0.01


def test_image_document_has_single_page(tmp_path: Path) -> None:
    original = tmp_path / "scan.png"
    Image.new("RGB", (800, 600), "white").save(original)
    service = PagePreviewService(original, "image/png", ANALYSIS_ID)

    assert [(s.width, s.height) for s in service.page_sizes()] == [(800, 600)]
    assert Image.open(service.get_preview("original", 1, "lg")).size == (800, 600)


def test_missing_page_or_variant_raises_not_found(tmp_path: Path) -> None:
    original = _make_pdf(tmp_path / "doc.pdf", 1)
    service = PagePreviewService(original, "application/pdf", ANALYSIS_ID)

    with pytest.raises(NotFoundError):
        service.get_preview("original", 2, "lg")
    with pytest.raises(NotFoundError):
        service.get_preview("redacted", 1, "lg")


def test_warm_and_delete(tmp_path: Path) -> None:
    original = _make_pdf(tmp_path / "doc.pdf", 2)
    _make_redacted_tiff(original, 2)
    service = PagePreviewService(original, "application/pdf", ANALYSIS_ID)

    service.warm()
    assert len(list(previews_dir(original, ANALYSIS_ID).glob("*.webp"))) == 8

    service.delete()
    assert not previews_dir(original, ANALYSIS_ID).exists()
