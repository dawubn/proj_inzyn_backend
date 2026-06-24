from __future__ import annotations

import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
import structlog
from PIL import Image, ImageDraw, ImageFont

from app.adapters.local_ocr import LocalOCRAdapter, WordDict
from app.core.config import settings
from app.services.redaction_detectors import find_personal_data

logger = structlog.get_logger(__name__)

_PDF_CONTENT_TYPE = "application/pdf"


@dataclass
class RedactionResult:
    output_path: Path
    media_type: str
    filename: str


class RedactionService:
    """Orchestrates local OCR, personal-data detection, and pixel-level masking."""

    def __init__(self) -> None:
        self._ocr = LocalOCRAdapter()

    def anonymize_file(
        self,
        input_path: Path,
        original_filename: str,
        content_type: str,
    ) -> RedactionResult:
        is_pdf = content_type == _PDF_CONTENT_TYPE

        if is_pdf:
            all_words, all_images = self._ocr.ocr_pdf(input_path)
        else:
            all_words, all_images = self._ocr.ocr_image(input_path)

        masked: list[Image.Image] = []
        total = 0
        for page_words, img in zip(all_words, all_images, strict=False):
            items = find_personal_data(page_words)
            total += len(items)
            masked.append(self._apply_masks(img, items))

        logger.info("Entities detected", total=total, file=original_filename)

        stem = Path(original_filename).stem
        if is_pdf:
            return RedactionResult(
                output_path=self._save_as_pdf(masked),
                media_type=_PDF_CONTENT_TYPE,
                filename=f"anonymized_{stem}.pdf",
            )
        return RedactionResult(
            output_path=self._save_as_png(masked[0]),
            media_type="image/png",
            filename=f"anonymized_{stem}.png",
        )

    def _apply_masks(self, image: Image.Image, items: list[WordDict]) -> Image.Image:
        img = image.copy()
        draw = ImageDraw.Draw(img)
        pad = settings.REDACTION_BOX_PADDING_PX

        try:
            font = ImageFont.truetype("arial.ttf", 11)
        except OSError:
            font = ImageFont.load_default()

        for item in items:
            x, y, w, h = item["x"], item["y"], item["szerokosc"], item["wysokosc"]
            x1, y1 = max(0, x - pad), max(0, y - pad)
            x2, y2 = min(img.width, x + w + pad), min(img.height, y + h + pad)
            draw.rectangle([x1, y1, x2, y2], fill="black")
            label: str = item.get("rodzaj_danych", "")
            if label:
                draw.text((x1 + 2, y1), label, fill="white", font=font)

        return img

    def _save_as_pdf(self, images: list[Image.Image]) -> Path:
        pdf = fitz.open()
        for img in images:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="PNG")
            page_img = fitz.open(stream=buf.getvalue(), filetype="png")
            page_pdf = fitz.open("pdf", page_img.convert_to_pdf())
            pdf.insert_pdf(page_pdf)
            page_img.close()
            page_pdf.close()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            path = Path(f.name)
        pdf.save(str(path))
        pdf.close()
        return path

    def _save_as_png(self, image: Image.Image) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            path = Path(f.name)
        image.convert("RGB").save(str(path), format="PNG")
        return path
