import io
from collections.abc import Iterator

import pytest
from PIL import Image

from app.adapters.azure_ocr import split_into_page_chunks
from app.services.redaction_service import RedactionService


def test_redaction_writes_and_releases_pages_in_order(tmp_path, monkeypatch):
    service = RedactionService()
    opened: list[Image.Image] = []

    def pages(_path):
        for index in range(5):
            if opened:
                with pytest.raises(ValueError):
                    opened[-1].getpixel((0, 0))
            image = Image.new("RGB", (40, 40), (index * 30, 255, 255))
            opened.append(image)
            yield [], image

    monkeypatch.setattr(service._ocr, "iter_pdf_pages", pages)
    monkeypatch.setattr("app.services.redaction_service.find_personal_data", lambda _: [])
    result = service.anonymize_file(tmp_path / "test.pdf", "test.pdf", "application/pdf")
    try:
        chunks, count = split_into_page_chunks(result.output_path.read_bytes(), "image/tiff", 2)
        assert count == 5
        assert len(chunks) == 3
        colors = []
        for chunk in chunks:
            with Image.open(io.BytesIO(chunk)) as tiff:
                for index in range(tiff.n_frames):
                    tiff.seek(index)
                    colors.append(tiff.getpixel((0, 0)))
        assert colors == [(index * 30, 255, 255) for index in range(5)]
        with pytest.raises(ValueError):
            opened[-1].getpixel((0, 0))
    finally:
        result.output_path.unlink()


def test_failed_redaction_removes_partial_output(tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def broken_pages() -> Iterator[Image.Image]:
        with Image.new("RGB", (20, 20)) as image:
            yield image
        raise RuntimeError("OCR failed on page 2")

    with pytest.raises(RuntimeError, match="page 2"):
        RedactionService()._save_as_multipage_tiff(broken_pages())
    assert list(tmp_path.glob("*.tiff")) == []
