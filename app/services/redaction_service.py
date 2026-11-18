from __future__ import annotations

import io
import re
import tempfile
from collections.abc import Collection
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
import structlog
from PIL import Image, ImageDraw, ImageFont

from app.adapters.local_ocr import (
    LocalOCRAdapter,
    PageRead,
    WordDict,
    rapidocr_words,
    second_ocr_available,
    tagged_words,
)
from app.core.config import settings
from app.enums.analysis import RedactionCategory
from app.services import redaction_ner
from app.services.redaction_detectors import detect_document, select_categories
from app.services.redaction_visual import find_visual_regions, ink_regions

logger = structlog.get_logger(__name__)

_PDF_CONTENT_TYPE = "application/pdf"
_LABEL_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "arial.ttf",
)
_MIN_LABEL_PX = 14
_BLANK_PAPER = 200
_PREFIX = "[UTAJNIONO: "
# Labels of visual regions win over word labels when both cover the same place.
_VISUAL_LABELS = ("PODPIS", "ZDJECIE", "KOD")
_SHAPE_COVERS_WORD = 0.9
_PRINT_HEIGHT = 1.35
_NOISE_OVERLAP = 0.3
# Words of one value are a space apart; a short word between two values stays visible.
_WORD_GAP = 0.5
_MOSTLY_MASKED = 0.8
# Our own label text read back, whole or in pieces, e.g. "UTAJNIONO:" or "NAZWISKO]".
_LABEL_TEXT = re.compile(r"UTAJN|[A-Z/ ]{2,}\]$")


@dataclass
class RedactionResult:
    output_path: Path
    media_type: str
    filename: str
    regions: list[list[WordDict]] = field(default_factory=list)
    name_stems: set[str] = field(default_factory=set)


@dataclass
class _Mask:
    x0: int
    y0: int
    x1: int
    y1: int
    label: str
    pixels: np.ndarray | None = None

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def as_region(self) -> WordDict:
        region: WordDict = {
            "x": self.x0,
            "y": self.y0,
            "szerokosc": self.x1 - self.x0,
            "wysokosc": self.y1 - self.y0,
            "rodzaj_danych": self.label,
        }
        if self.pixels is not None:
            region["piksele"] = self.pixels
        return region


def _is_visual(label: str) -> bool:
    return any(name in label for name in _VISUAL_LABELS)


def _overlap_share(a: _Mask, b: _Mask) -> float:
    w = min(a.x1, b.x1) - max(a.x0, b.x0)
    h = min(a.y1, b.y1) - max(a.y0, b.y0)
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a.x1 - a.x0) * a.height, (b.x1 - b.x0) * b.height)
    return w * h / max(1, smaller)


def _same_line_neighbours(a: _Mask, b: _Mask) -> bool:
    if a.label != b.label or not a.label:
        return False
    vertical = min(a.y1, b.y1) - max(a.y0, b.y0)
    if vertical < 0.5 * min(a.height, b.height):
        return False
    gap = max(a.x0, b.x0) - min(a.x1, b.x1)
    return gap <= _WORD_GAP * min(a.height, b.height)


def _merge(masks: list[_Mask]) -> list[_Mask]:
    """One rectangle per value: joins words of the same entity and duplicates across sources."""
    merged = list(masks)
    changed = True
    while changed:
        changed = False
        result: list[_Mask] = []
        for mask in merged:
            for kept in result:
                if _overlap_share(mask, kept) >= 0.5 or _same_line_neighbours(mask, kept):
                    label = kept.label
                    if _is_visual(mask.label) or (not label and mask.label):
                        label = mask.label
                    kept.x0, kept.y0 = min(kept.x0, mask.x0), min(kept.y0, mask.y0)
                    kept.x1, kept.y1 = max(kept.x1, mask.x1), max(kept.y1, mask.y1)
                    kept.label = label
                    changed = True
                    break
            else:
                result.append(mask)
        merged = result
    return merged


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in _LABEL_FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _label_layout(label: str, width: int, height: int) -> tuple[list[str], int] | None:
    """Largest font size at which the label fits the box, on one line or on two."""
    name = label[len(_PREFIX) : -1] if label.startswith(_PREFIX) else label
    layouts = ([label], [_PREFIX.strip(), f"{name}]"])
    for lines in layouts:
        size = int(min(0.62 * height / len(lines), 64))
        while size >= _MIN_LABEL_PX:
            font = _font(size)
            if all(font.getlength(line) <= width - 6 for line in lines):
                return lines, size
            size -= 1
    return None


def _label_box(mask: _Mask) -> _Mask:
    """Where the label goes: the whole box, or the lower band of a signature."""
    if mask.pixels is None:
        return mask
    band = max(_MIN_LABEL_PX * 2, mask.height // 3)
    return _Mask(mask.x0, max(mask.y0, mask.y1 - band), mask.x1, mask.y1, mask.label)


def _widen_for_label(image: Image.Image, mask: _Mask) -> _Mask:
    """Extends a box over blank paper on the right when its label does not fit inside."""
    if mask.pixels is not None or not mask.label:
        return mask
    if _label_layout(mask.label, mask.x1 - mask.x0, mask.height) is not None:
        return mask
    font = _font(max(_MIN_LABEL_PX, int(0.5 * mask.height)))
    needed = int(font.getlength(mask.label)) + 8
    right = min(image.width, mask.x0 + needed)
    if right <= mask.x1:
        return mask
    strip = np.asarray(image.convert("L").crop((mask.x1, mask.y0, right, mask.y1)))
    if strip.size and strip.min() > _BLANK_PAPER:
        return _Mask(mask.x0, mask.y0, right, mask.y1, mask.label)
    return mask


def _draw(image: Image.Image, masks: list[_Mask]) -> Image.Image:
    masks = [_widen_for_label(image, mask) for mask in masks]
    img = image.copy()
    draw = ImageDraw.Draw(img)
    for mask in masks:
        if mask.pixels is not None:
            shape = Image.fromarray(mask.pixels.astype(np.uint8) * 255, mode="L")
            img.paste((0, 0, 0), (mask.x0, mask.y0), shape)
        else:
            draw.rectangle([mask.x0, mask.y0, mask.x1, mask.y1], fill="black")
    for mask in masks:
        if not mask.label:
            continue
        box = _label_box(mask)
        layout = _label_layout(box.label, box.x1 - box.x0, box.height)
        if layout is None:
            continue
        lines, size = layout
        font = _font(size)
        step = size * 1.15
        if mask.pixels is not None:
            width = max(font.getlength(line) for line in lines) + 6
            draw.rectangle([box.x0, box.y0, box.x0 + width, box.y1], fill="black")
        top = box.y0 + (box.height - step * len(lines)) / 2
        for index, line in enumerate(lines):
            draw.text((box.x0 + 3, top + index * step), line, fill="white", font=font)
    return img


def _to_masks(items: list[WordDict], width: int, height: int) -> list[_Mask]:
    masks = []
    for item in items:
        if item.get("piksele") is not None:
            masks.append(
                _Mask(
                    item["x"],
                    item["y"],
                    item["x"] + item["szerokosc"],
                    item["y"] + item["wysokosc"],
                    str(item["rodzaj_danych"]),
                    item["piksele"],
                )
            )
            continue
        x, y, w, h = item["x"], item["y"], item["szerokosc"], item["wysokosc"]
        # Glyphs reach past the OCR box; the padding follows the size of the text.
        pad = min(2 * settings.REDACTION_BOX_PADDING_PX, max(4, int(0.3 * h)))
        masks.append(
            _Mask(
                max(0, x - pad),
                max(0, y - pad),
                min(width, x + w + pad),
                min(height, y + h + pad),
                str(item.get("rodzaj_danych", "")),
            )
        )
    return masks


def _line_height(words: list[WordDict]) -> float:
    heights = sorted(w["wysokosc"] for w in words if any(c.isalpha() for c in w["tekst"]))
    return float(heights[len(heights) // 2]) if heights else 0.0


def _touches_any(item: WordDict, regions: list[WordDict]) -> bool:
    x0, y0 = item["x"], item["y"]
    x1, y1 = x0 + item["szerokosc"], y0 + item["wysokosc"]
    area = max(1, (x1 - x0) * (y1 - y0))
    for region in regions:
        w = min(x1, region["x"] + region["szerokosc"]) - max(x0, region["x"])
        h = min(y1, region["y"] + region["wysokosc"]) - max(y0, region["y"])
        if w > 0 and h > 0 and w * h / area >= _NOISE_OVERLAP:
            return True
    return False


def _hidden_by_shapes(item: WordDict, shapes: list[WordDict]) -> bool:
    """A word whose pixels the signature mask already covers almost entirely."""
    x0, y0 = item["x"], item["y"]
    x1, y1 = x0 + item["szerokosc"], y0 + item["wysokosc"]
    area = max(1, (x1 - x0) * (y1 - y0))
    covered = 0
    for shape in shapes:
        sx, sy, pixels = shape["x"], shape["y"], shape["piksele"]
        left, top = max(x0, sx), max(y0, sy)
        right, bottom = min(x1, sx + pixels.shape[1]), min(y1, sy + pixels.shape[0])
        if right > left and bottom > top:
            covered += int(pixels[top - sy : bottom - sy, left - sx : right - sx].sum())
    return bool(covered / area >= _SHAPE_COVERS_WORD)


def _blank_masked(words: list[WordDict], masks: list[_Mask]) -> list[WordDict]:
    """Second reading with our label text replaced, so half visible values are still found."""

    def touches_mask(item: WordDict) -> bool:
        x0, y0 = item["x"], item["y"]
        x1, y1 = x0 + item["szerokosc"], y0 + item["wysokosc"]
        area = max(1, (x1 - x0) * (y1 - y0))
        for mask in masks:
            w = min(x1, mask.x1) - max(x0, mask.x0)
            h = min(y1, mask.y1) - max(y0, mask.y0)
            if w > 0 and h > 0 and w * h / area >= _MOSTLY_MASKED:
                return True
        return False

    return [
        (
            {**word, "tekst": "[UTAJNIONO]"}
            if _LABEL_TEXT.search(str(word["tekst"])) or touches_mask(word)
            else word
        )
        for word in words
    ]


class RedactionService:
    """Orchestrates local OCR, personal-data detection, and pixel-level masking."""

    def __init__(self) -> None:
        self._ocr = LocalOCRAdapter()

    def anonymize_file(
        self,
        input_path: Path,
        original_filename: str,
        content_type: str,
        categories: Collection[RedactionCategory] | None = None,
    ) -> RedactionResult:
        """Mask personal data and save the pages as a multi-page TIFF.

        `categories` limits masking to these kinds of data (None means all kinds).
        An empty collection turns masking off: the pages are only converted,
        without running the local OCR.
        """
        stem = Path(original_filename).stem
        if categories is not None and not categories:
            return RedactionResult(
                output_path=self._save_as_multipage_tiff(
                    self._render_only(input_path, content_type)
                ),
                media_type="image/tiff",
                filename=f"anonymized_{stem}.tiff",
                regions=[[]],
            )

        self._check_models()
        if content_type == _PDF_CONTENT_TYPE:
            pages = self._ocr.read_pdf(input_path)
        else:
            pages = self._ocr.read_image_file(input_path)

        detected, name_stems = detect_document([page.words for page in pages])
        detected = [select_categories(items, categories) for items in detected]

        masked: list[Image.Image] = []
        regions: list[list[WordDict]] = []
        for number, (page, items) in enumerate(zip(pages, detected, strict=True), start=1):
            page_image, page_masks = self._mask_page(page, items, name_stems, categories)
            masked.append(page.restore_image(page_image))
            regions.append(page.restore_boxes([mask.as_region() for mask in page_masks]))
            area = sum((m.x1 - m.x0) * m.height for m in page_masks)
            logger.info(
                "Page redacted",
                page=number,
                masks=len(page_masks),
                masked_share=round(area / max(1, page.image.width * page.image.height), 4),
                rotation=page.rotation,
            )

        # Save all pages as multi-page TIFF
        return RedactionResult(
            output_path=self._save_as_multipage_tiff(masked),
            media_type="image/tiff",
            filename=f"anonymized_{stem}.tiff",
            regions=regions,
            # Names left visible on purpose must not be removed from the text later
            name_stems=(
                name_stems
                if categories is None or RedactionCategory.PERSON in categories
                else set()
            ),
        )

    def _render_only(self, input_path: Path, content_type: str) -> list[Image.Image]:
        if content_type == _PDF_CONTENT_TYPE:
            return self._ocr.ocr_pdf(input_path, recognize=False)[1]
        return self._ocr.ocr_image(input_path, recognize=False)[1]

    def _check_models(self) -> None:
        missing = [
            name
            for name, ready in (
                ("HerBERT NER", redaction_ner.herbert_available()),
                ("RapidOCR", second_ocr_available()),
            )
            if not ready
        ]
        if not missing:
            return
        if settings.REDACTION_REQUIRE_MODELS:
            raise RuntimeError(f"Redaction models unavailable: {', '.join(missing)}")
        logger.warning("Redaction runs without some models", missing=missing)

    def _mask_page(
        self,
        page: PageRead,
        items: list[WordDict],
        name_stems: set[str],
        categories: Collection[RedactionCategory] | None = None,
    ) -> tuple[Image.Image, list[_Mask]]:
        width, height = page.image.size
        visual = find_visual_regions(page.image, page.words)
        shapes = [r for r in visual if r.get("piksele") is not None]
        boxes = [r for r in visual if r.get("piksele") is None]
        # OCR noise read from a signature: taller than print and over a signature shape.
        # It is masked by its ink only, so the printed line it overlaps stays readable.
        line_height = _line_height(page.words)
        noise = [
            i
            for i in items
            if shapes
            and i["wysokosc"] > _PRINT_HEIGHT * line_height
            and _touches_any(i, shapes)
            and not _hidden_by_shapes(i, shapes)
        ]
        items = [i for i in items if i not in noise and not _hidden_by_shapes(i, shapes)]
        shapes += ink_regions(page.image, page.words, noise)
        masks = _merge(_to_masks(items + boxes, width, height)) + _to_masks(shapes, width, height)
        image = _draw(page.image, masks)
        if not settings.REDACTION_VERIFY_ENABLED:
            return image, masks

        # Read the masked page again; whatever is still recognisable gets masked too.
        words = _blank_masked(tagged_words(rapidocr_words(image), "verify"), masks)
        missed = select_categories(detect_document([words], name_stems)[0][0], categories)
        if missed:
            logger.warning(
                "Verification added masks",
                count=len(missed),
                labels=sorted({str(m["rodzaj_danych"]) for m in missed}),
            )
            plain = [m for m in masks if m.pixels is None]
            shaped = [m for m in masks if m.pixels is not None]
            masks = _merge(plain + _to_masks(missed, width, height)) + shaped
            image = _draw(page.image, masks)
        return image, masks

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

    def _save_as_multipage_tiff(self, images: list[Image.Image]) -> Path:
        with tempfile.NamedTemporaryFile(suffix=".tiff", delete=False) as f:
            path = Path(f.name)

        rgb_images = [img.convert("RGB") for img in images]
        if rgb_images:
            rgb_images[0].save(
                str(path),
                format="TIFF",
                save_all=True,
                append_images=rgb_images[1:] if len(rgb_images) > 1 else [],
                compression="tiff_adobe_deflate",
            )
        return path
