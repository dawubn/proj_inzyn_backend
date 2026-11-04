"""
Lightweight WebP previews of document pages for the analysis viewer.

Both variants share the page geometry used by OCR: the redacted TIFF frames are renders of
the original pages, so coordinates normalized to a page (0-1) line up on either variant.
Previews are rendered on first request (or warmed by the analysis task) and cached on disk.
"""

from __future__ import annotations

import io
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz  # PyMuPDF
import structlog
from PIL import Image, ImageSequence

from app.core.exceptions import NotFoundError

logger = structlog.get_logger(__name__)

PreviewVariant = Literal["original", "redacted"]
PreviewSize = Literal["sm", "lg"]

_LONG_SIDE_PX: dict[PreviewSize, int] = {"sm": 1000, "lg": 2000}
_WEBP_QUALITY = 80
_PDF_CONTENT_TYPE = "application/pdf"


@dataclass(frozen=True)
class PageSize:
    page: int
    width: float
    height: float


def redacted_path(original_path: Path, analysis_id: str) -> Path:
    """Location of the redacted multi-page TIFF produced by the legal analysis task."""
    return original_path.parent / f"redacted_{analysis_id}.tiff"


def previews_dir(original_path: Path, analysis_id: str) -> Path:
    return original_path.parent / "previews" / analysis_id


class PagePreviewService:
    def __init__(self, original_path: Path, mime_type: str, analysis_id: str) -> None:
        self._original_path = original_path
        self._mime_type = mime_type
        self._analysis_id = analysis_id
        self._redacted_path = redacted_path(original_path, analysis_id)

    def available_variants(self) -> list[PreviewVariant]:
        variants: list[PreviewVariant] = []
        if self._original_path.exists():
            variants.append("original")
        if self._redacted_path.exists():
            variants.append("redacted")
        return variants

    def page_sizes(self) -> list[PageSize]:
        """Page dimensions; only their aspect ratio is meaningful to clients."""
        if self._redacted_path.exists():
            return _image_page_sizes(self._redacted_path)
        if not self._original_path.exists():
            raise NotFoundError("Document file not found on storage")
        if self._mime_type == _PDF_CONTENT_TYPE:
            with fitz.open(str(self._original_path)) as doc:
                return [
                    PageSize(page=i + 1, width=page.rect.width, height=page.rect.height)
                    for i, page in enumerate(doc)
                ]
        return _image_page_sizes(self._original_path)

    def get_preview(self, variant: PreviewVariant, page: int, size: PreviewSize) -> Path:
        """Return the cached preview of a page (1-based), rendering it if needed."""
        cached = self._cache_path(variant, page, size)
        if not cached.exists():
            self._render_and_cache(variant, page)
        if not cached.exists():
            raise NotFoundError("Page not found")
        return cached

    def warm(self) -> None:
        """Render previews of every page of every available variant."""
        for variant in self.available_variants():
            for page_size in self.page_sizes():
                if not self._cache_path(variant, page_size.page, "lg").exists():
                    self._render_and_cache(variant, page_size.page)

    def delete(self) -> None:
        shutil.rmtree(previews_dir(self._original_path, self._analysis_id), ignore_errors=True)

    def _cache_path(self, variant: PreviewVariant, page: int, size: PreviewSize) -> Path:
        return (
            previews_dir(self._original_path, self._analysis_id) / f"{variant}_{page}_{size}.webp"
        )

    def _render_and_cache(self, variant: PreviewVariant, page: int) -> None:
        image = self._render(variant, page)
        target_dir = previews_dir(self._original_path, self._analysis_id)
        target_dir.mkdir(parents=True, exist_ok=True)

        for size, long_side in _LONG_SIDE_PX.items():
            resized = image.copy()
            resized.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
            # Write to a temp file first so concurrent requests never read a partial image
            with tempfile.NamedTemporaryFile(dir=target_dir, suffix=".tmp", delete=False) as tmp:
                resized.save(tmp, format="WEBP", quality=_WEBP_QUALITY, method=4)
            Path(tmp.name).replace(self._cache_path(variant, page, size))

        logger.info("Page preview rendered", variant=variant, page=page, size=image.size)

    def _render(self, variant: PreviewVariant, page: int) -> Image.Image:
        if variant == "redacted":
            if not self._redacted_path.exists():
                raise NotFoundError("Redacted document not available")
            return _image_frame(self._redacted_path, page)

        if not self._original_path.exists():
            raise NotFoundError("Document file not found on storage")
        if self._mime_type == _PDF_CONTENT_TYPE:
            return _render_pdf_page(self._original_path, page)
        return _image_frame(self._original_path, page)


def _render_pdf_page(path: Path, page: int) -> Image.Image:
    with fitz.open(str(path)) as doc:
        if not 1 <= page <= len(doc):
            raise NotFoundError("Page not found")
        pdf_page = doc.load_page(page - 1)
        zoom = _LONG_SIDE_PX["lg"] / max(pdf_page.rect.width, pdf_page.rect.height)
        pix = pdf_page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")


def _image_frame(path: Path, page: int) -> Image.Image:
    with Image.open(path) as image:
        frame_count = getattr(image, "n_frames", 1)
        if not 1 <= page <= frame_count:
            raise NotFoundError("Page not found")
        image.seek(page - 1)
        return image.convert("RGB")


def _image_page_sizes(path: Path) -> list[PageSize]:
    with Image.open(path) as image:
        return [
            PageSize(page=i + 1, width=frame.width, height=frame.height)
            for i, frame in enumerate(ImageSequence.Iterator(image))
        ]
