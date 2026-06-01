from __future__ import annotations

import io
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
                }
            )
            used.add(id(current))
            logger.debug("Merged split number", text=result[-1]["tekst"])
        else:
            result.append(current)

    return result


def _ocr_page(image_bytes: bytes) -> tuple[list[WordDict], Image.Image]:
    orig = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    bordered = ImageOps.expand(orig, border=_BORDER_PX, fill="white")

    data: dict[str, Any] = pytesseract.image_to_data(  # type: ignore[assignment]
        bordered,
        lang=settings.LOCAL_OCR_LANG,
        config=_TESSERACT_CONFIG,
        output_type=pytesseract.Output.DICT,
    )

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
            }
        )

    return _post_processing(words), orig


class LocalOCRAdapter:
    """Renders PDF/image to PIL Images and extracts per-word bounding boxes via Tesseract."""

    def ocr_pdf(self, path: Path) -> tuple[list[list[WordDict]], list[Image.Image]]:
        logger.info("Rendering PDF for OCR", path=str(path), dpi=settings.LOCAL_OCR_DPI)
        doc = fitz.open(str(path))
        zoom = settings.LOCAL_OCR_DPI / 72
        matrix = fitz.Matrix(zoom, zoom)
        all_words: list[list[WordDict]] = []
        all_images: list[Image.Image] = []
        for page_id in range(len(doc)):
            png: bytes = doc.load_page(page_id).get_pixmap(matrix=matrix).tobytes("png")
            words, img = _ocr_page(png)
            all_words.append(words)
            all_images.append(img)
            logger.debug("OCR page done", page=page_id + 1, words=len(words))
        doc.close()
        return all_words, all_images

    def ocr_image(self, path: Path) -> tuple[list[list[WordDict]], list[Image.Image]]:
        logger.info("Running OCR on image", path=str(path))
        with path.open("rb") as f:
            words, img = _ocr_page(f.read())
        return [words], [img]
