from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pytesseract
import structlog
from PIL import Image, ImageOps

from app.core.config import settings

logger = structlog.get_logger(__name__)

WordDict = dict[str, Any]

_BORDER_PX = 30  # prevents Tesseract from clipping digits at page edges
_MERGE_Y_TOLERANCE = 15  # max pixel difference in Y for two fragments to be on the same line
_MERGE_X_GAP = 40  # max pixel gap between adjacent digit fragments to be merged
_TESSERACT_CONFIG = (
    "--psm 3 "
    "-c language_model_penalty_non_freq_dict_word=0.5 "
    "-c language_model_penalty_non_dict_word=0.5 "
    "-c segment_penalty_dict_nonword=1000"  # prevents breaking digit sequences
)


def _post_processing(words: list[WordDict]) -> list[WordDict]:
    """Merge geometrically adjacent digit fragments into a single word (e.g. split PESEL)."""
    if not words:
        return words

    result: list[WordDict] = []
    used: set[int] = set()

    for idx, current in enumerate(words):
        if id(current) in used:
            continue

        if not current["tekst"].isdigit():
            result.append(current)
            continue

        group = [current]
        for candidate in words[idx + 1 :]:
            if id(candidate) in used or not candidate["tekst"].isdigit():
                continue
            same_line = abs(current["y"] - candidate["y"]) <= _MERGE_Y_TOLERANCE
            gap = candidate["x"] - (current["x"] + current["szerokosc"])
            reverse_gap = current["x"] - (candidate["x"] + candidate["szerokosc"])
            if same_line and gap <= _MERGE_X_GAP and reverse_gap <= _MERGE_X_GAP:
                group.append(candidate)
                used.add(id(candidate))

        if len(group) > 1:
            by_x = sorted(group, key=lambda e: e["x"])
            result.append(
                {
                    "tekst": "".join(e["tekst"] for e in by_x),
                    "x": min(e["x"] for e in group),
                    "y": min(e["y"] for e in group),
                    "szerokosc": sum(e["szerokosc"] for e in group),
                    "wysokosc": max(e["y"] + e["wysokosc"] for e in group)
                    - min(e["y"] for e in group),
                    "linia": current.get("linia"),
                }
            )
            used.add(id(current))
            logger.debug("Merged split number", text=result[-1]["tekst"])
        else:
            result.append(current)

    return result


def _ocr_page(image_bytes: bytes) -> tuple[list[WordDict], Image.Image]:
    orig = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    try:
        with ImageOps.expand(orig, border=_BORDER_PX, fill="white") as bordered:
            data: dict[str, Any] = pytesseract.image_to_data(
                bordered,
                lang=settings.LOCAL_OCR_LANG,
                config=_TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT,
                timeout=settings.REDACTION_TIMEOUT_SECONDS,
            )
    except BaseException:
        orig.close()
        raise

    words: list[WordDict] = []
    for i in range(len(data["text"])):
        word = str(data["text"][i]).strip()
        if not word:
            continue
        try:
            x = int(data["left"][i]) - _BORDER_PX
            y = int(data["top"][i]) - _BORDER_PX
        except (ValueError, TypeError):
            continue
        words.append(
            {
                "tekst": word,
                "x": max(0, x),
                "y": max(0, y),
                "szerokosc": int(data["width"][i]),
                "wysokosc": int(data["height"][i]),
                "linia": f"{data['block_num'][i]}-{data['par_num'][i]}-{data['line_num'][i]}",
            }
        )

    return _post_processing(words), orig


def page_render_zoom(page: fitz.Page, zoom: float) -> float:
    """Caps zoom so the longest rendered side stays within LOCAL_OCR_MAX_PAGE_PX."""
    longest_side_pt = float(max(page.rect.width, page.rect.height))
    if longest_side_pt <= 0:
        return zoom
    return min(zoom, settings.LOCAL_OCR_MAX_PAGE_PX / longest_side_pt)


class LocalOCRAdapter:
    """Renders PDF/image to PIL Images and extracts per-word bounding boxes via Tesseract."""

    def ocr_pdf(self, path: Path) -> tuple[list[list[WordDict]], list[Image.Image]]:
        pages = list(self.iter_pdf_pages(path))
        return [words for words, _ in pages], [image for _, image in pages]

    def iter_pdf_pages(self, path: Path) -> Iterator[tuple[list[WordDict], Image.Image]]:
        """Yield pages individually so redaction can release each raster before the next."""
        logger.info("Rendering PDF for OCR", path=str(path), dpi=settings.LOCAL_OCR_DPI)
        with fitz.open(str(path)) as doc:
            yield from self._ocr_pdf_pages(doc)

    def _ocr_pdf_pages(self, doc: fitz.Document) -> Iterator[tuple[list[WordDict], Image.Image]]:
        initial_zoom = settings.LOCAL_OCR_DPI / 72

        for page_id in range(len(doc)):
            zoom = page_render_zoom(doc.load_page(page_id), initial_zoom)
            max_retries = 3
            min_zoom = 1.5
            zoom_reduction_factor = 1.5

            for attempt in range(max_retries):
                try:
                    matrix = fitz.Matrix(zoom, zoom)
                    png: bytes = doc.load_page(page_id).get_pixmap(matrix=matrix).tobytes("png")
                    words, img = _ocr_page(png)
                    logger.debug("OCR page done", page=page_id + 1, words=len(words), zoom=zoom)
                    yield words, img
                    break
                except Image.DecompressionBombError:
                    if zoom <= min_zoom or attempt >= max_retries - 1:
                        logger.exception(
                            "Failed to render page even with reduced zoom",
                            page=page_id + 1,
                            zoom=zoom,
                        )
                        raise
                    zoom = zoom / zoom_reduction_factor
                    logger.warning(
                        "Reducing zoom due to decompression bomb",
                        page=page_id + 1,
                        old_zoom=round(zoom * zoom_reduction_factor, 3),
                        new_zoom=round(zoom, 3),
                    )

    def ocr_image(self, path: Path) -> tuple[list[list[WordDict]], list[Image.Image]]:
        logger.info("Running OCR on image", path=str(path))
        with path.open("rb") as f:
            words, img = _ocr_page(f.read())
        return [words], [img]
