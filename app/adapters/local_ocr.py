from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import numpy as np
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
_OSD_MIN_CONFIDENCE = 1.5
_READ_CHECK_PX = 1800
_MIN_CONFIDENCE_GAIN = 10.0
_MIN_WORD_LENGTH = 3
_RIGHT_ANGLES = (90, 180, 270)

SOURCE_TESSERACT = "tesseract"
SOURCE_RAPIDOCR = "rapidocr"
SOURCE_PDF_TEXT = "pdf_text"


@dataclass
class PageRead:
    """Upright copy of a page with words from every source."""

    image: Image.Image
    words: list[WordDict] = field(default_factory=list)
    rotation: int = 0

    def restore_image(self, image: Image.Image) -> Image.Image:
        return image.rotate(self.rotation, expand=True) if self.rotation else image

    def restore_boxes(self, boxes: list[WordDict]) -> list[WordDict]:
        if not self.rotation:
            return boxes
        restored = _rotate_words_back(boxes, self.rotation, self.image.size)
        for box in restored:
            if box.get("piksele") is not None:
                box["piksele"] = np.rot90(box["piksele"], k=self.rotation // 90)
        return restored


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
                    **current,
                    "tekst": "".join(e["tekst"] for e in by_x),
                    "x": min(e["x"] for e in group),
                    "y": min(e["y"] for e in group),
                    "szerokosc": max(e["x"] + e["szerokosc"] for e in group)
                    - min(e["x"] for e in group),
                    "wysokosc": max(e["y"] + e["wysokosc"] for e in group)
                    - min(e["y"] for e in group),
                }
            )
            used.add(id(current))
            logger.debug("Merged split number", text=result[-1]["tekst"])
        else:
            result.append(current)

    return result


def _tesseract_words(image: Image.Image) -> list[WordDict]:
    bordered = ImageOps.expand(image, border=_BORDER_PX, fill="white")
    data: dict[str, Any] = pytesseract.image_to_data(
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
                "linia": f"{data['block_num'][i]}-{data['par_num'][i]}-{data['line_num'][i]}",
            }
        )
    return _post_processing(words)


def _ocr_page(image_bytes: bytes) -> tuple[list[WordDict], Image.Image]:
    orig = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    rotation = _orientation(orig)
    if not rotation:
        return _tesseract_words(orig), orig
    upright = orig.rotate(-rotation, expand=True)
    return _rotate_words_back(_tesseract_words(upright), rotation, upright.size), orig


def _orientation(image: Image.Image) -> int:
    """Clockwise turn that makes the page upright, checked by Tesseract confidence."""
    try:
        osd = pytesseract.image_to_osd(image, config="--psm 0", output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        # Too little text to decide, e.g. a photo or an empty page.
        return 0
    rotation = int(osd.get("rotate", 0))
    if rotation not in _RIGHT_ANGLES or float(osd.get("orientation_conf", 0)) < _OSD_MIN_CONFIDENCE:
        return 0
    small = image.convert("L")
    small.thumbnail((_READ_CHECK_PX, _READ_CHECK_PX))
    as_is = _reading_confidence(small)
    turned = _reading_confidence(small.rotate(-rotation, expand=True))
    if turned < as_is + _MIN_CONFIDENCE_GAIN:
        logger.info("Page rotation rejected", rotation=rotation, as_is=as_is, turned=turned)
        return 0
    logger.info("Rotated page detected", rotation=rotation, as_is=as_is, turned=turned)
    return rotation


def _reading_confidence(image: Image.Image) -> float:
    data = pytesseract.image_to_data(
        image, lang=settings.LOCAL_OCR_LANG, config="--psm 3", output_type=pytesseract.Output.DICT
    )
    scores = [
        float(conf)
        for conf, text in zip(data["conf"], data["text"], strict=False)
        if float(conf) >= 0 and len(str(text).strip()) >= _MIN_WORD_LENGTH
    ]
    return sum(scores) / len(scores) if scores else 0.0


def _rotate_words_back(
    words: list[WordDict], rotation: int, upright_size: tuple[int, int]
) -> list[WordDict]:
    """Boxes read on the upright copy, moved back onto the page as it was supplied."""
    width, height = upright_size

    def back(x: float, y: float) -> tuple[float, float]:
        # The supplied page is the upright copy turned counterclockwise by `rotation`.
        if rotation == 90:
            return y, width - x
        if rotation == 180:
            return width - x, height - y
        return height - y, x

    result = []
    for word in words:
        x0, y0 = back(word["x"], word["y"])
        x1, y1 = back(word["x"] + word["szerokosc"], word["y"] + word["wysokosc"])
        result.append(
            {
                **word,
                "x": int(min(x0, x1)),
                "y": int(min(y0, y1)),
                "szerokosc": int(abs(x1 - x0)),
                "wysokosc": int(abs(y1 - y0)),
            }
        )
    return result


_rapid_engine: Any = None
_rapid_loaded = False


def _load_rapidocr() -> Any:
    global _rapid_engine, _rapid_loaded  # noqa: PLW0603
    if _rapid_loaded:
        return _rapid_engine
    if not settings.REDACTION_SECOND_OCR_ENABLED:
        _rapid_loaded = True
        return None
    try:
        from rapidocr import RapidOCR

        _rapid_engine = RapidOCR(
            params={
                "Global.log_level": "error",
                "Global.model_root_dir": str(Path(settings.REDACTION_MODELS_DIR) / "rapidocr"),
                "Det.lang_type": "pl",
                "Rec.lang_type": "pl",
                "EngineConfig.onnxruntime.intra_op_num_threads": settings.REDACTION_NER_THREADS,
                "EngineConfig.onnxruntime.inter_op_num_threads": 1,
            }
        )
    except Exception:
        # Not marked as loaded, so the next document tries again.
        logger.exception("RapidOCR unavailable, using Tesseract only")
        return None
    _rapid_loaded = True
    return _rapid_engine


def second_ocr_available() -> bool:
    return _load_rapidocr() is not None


def rapidocr_words(image: Image.Image) -> list[WordDict]:
    """Words from the PP-OCR models; better than Tesseract on photos and handwriting."""
    engine = _load_rapidocr()
    if engine is None:
        return []
    output = engine(np.array(image.convert("RGB")), return_word_box=True)
    words: list[WordDict] = []
    for line_index, line in enumerate(output.word_results or ()):
        for text, score, box in line or ():
            xs = [point[0] for point in box]
            ys = [point[1] for point in box]
            if not str(text).strip():
                continue
            words.append(
                {
                    "tekst": str(text).strip(),
                    "x": int(min(xs)),
                    "y": int(min(ys)),
                    "szerokosc": int(max(xs) - min(xs)),
                    "wysokosc": int(max(ys) - min(ys)),
                    "linia": f"r{line_index}",
                    "pewnosc": float(score),
                }
            )
    return words


def _pdf_text_words(page: fitz.Page, zoom: float) -> list[WordDict]:
    """Exact words of a digital PDF, positioned on the rendered page image."""
    transform = page.rotation_matrix * fitz.Matrix(zoom, zoom)
    words: list[WordDict] = []
    for x0, y0, x1, y1, text, block, line, _ in page.get_text("words"):
        if not str(text).strip():
            continue
        rect = fitz.Rect(x0, y0, x1, y1) * transform
        words.append(
            {
                "tekst": str(text),
                "x": int(rect.x0),
                "y": int(rect.y0),
                "szerokosc": int(rect.width),
                "wysokosc": int(rect.height),
                "linia": f"p{block}-{line}",
            }
        )
    return words


_GLUED = re.compile(r"^([^\W\d_][\w.\-]+:)(\S.*)$")


def _split_glued(word: WordDict) -> list[WordDict]:
    """Splits a caption glued to its value, e.g. "PESEL:92031504567"."""
    match = _GLUED.match(word["tekst"])
    if not match:
        return [word]
    head, tail = match.groups()
    cut = int(word["szerokosc"] * len(head) / len(word["tekst"]))
    # Characters differ in width, so the value starts half a character early rather than late.
    start = max(0, int(cut - 0.5 * cut / len(head)))
    return [
        {**word, "tekst": head, "szerokosc": cut},
        {**word, "tekst": tail, "x": word["x"] + start, "szerokosc": word["szerokosc"] - start},
    ]


def tagged_words(words: list[WordDict], source: str) -> list[WordDict]:
    """Words of one source, with captions glued to their values split apart."""
    return [{**part, "zrodlo": source} for word in words for part in _split_glued(word)]


def _rotate_words(words: list[WordDict], rotation: int, size: tuple[int, int]) -> list[WordDict]:
    """Boxes on the supplied page moved onto its upright copy (clockwise turn)."""
    width, height = size

    def turn(x: float, y: float) -> tuple[float, float]:
        if rotation == 90:
            return height - y, x
        if rotation == 180:
            return width - x, height - y
        return y, width - x

    result = []
    for word in words:
        x0, y0 = turn(word["x"], word["y"])
        x1, y1 = turn(word["x"] + word["szerokosc"], word["y"] + word["wysokosc"])
        result.append(
            {
                **word,
                "x": int(min(x0, x1)),
                "y": int(min(y0, y1)),
                "szerokosc": int(abs(x1 - x0)),
                "wysokosc": int(abs(y1 - y0)),
            }
        )
    return result


def read_image(image: Image.Image, pdf_words: list[WordDict] | None = None) -> PageRead:
    """All local readings of one page, turned upright when the page is rotated."""
    rotation = _orientation(image)
    upright = image.rotate(-rotation, expand=True) if rotation else image
    words = tagged_words(_tesseract_words(upright), SOURCE_TESSERACT)
    words += tagged_words(rapidocr_words(upright), SOURCE_RAPIDOCR)
    if pdf_words:
        placed = _rotate_words(pdf_words, rotation, image.size) if rotation else pdf_words
        words += tagged_words(placed, SOURCE_PDF_TEXT)
    return PageRead(image=upright, words=words, rotation=rotation)


def page_render_zoom(page: fitz.Page, zoom: float) -> float:
    """Caps zoom so the longest rendered side stays within LOCAL_OCR_MAX_PAGE_PX."""
    longest_side_pt = float(max(page.rect.width, page.rect.height))
    if longest_side_pt <= 0:
        return zoom
    return min(zoom, settings.LOCAL_OCR_MAX_PAGE_PX / longest_side_pt)


def _render_page(doc: fitz.Document, page_id: int) -> tuple[Image.Image, float]:
    zoom = page_render_zoom(doc.load_page(page_id), settings.LOCAL_OCR_DPI / 72)
    max_retries = 3
    min_zoom = 1.5
    zoom_reduction_factor = 1.5

    for attempt in range(max_retries):
        try:
            matrix = fitz.Matrix(zoom, zoom)
            png: bytes = doc.load_page(page_id).get_pixmap(matrix=matrix).tobytes("png")
            return Image.open(io.BytesIO(png)).convert("RGB"), zoom
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
    raise RuntimeError("Page could not be rendered")


class LocalOCRAdapter:
    """Renders PDF/image to PIL Images and extracts per-word bounding boxes."""

    def ocr_pdf(self, path: Path) -> tuple[list[list[WordDict]], list[Image.Image]]:
        logger.info("Rendering PDF for OCR", path=str(path), dpi=settings.LOCAL_OCR_DPI)
        all_words: list[list[WordDict]] = []
        all_images: list[Image.Image] = []
        with fitz.open(str(path)) as doc:
            for page_id in range(len(doc)):
                image, zoom = _render_page(doc, page_id)
                buf = io.BytesIO()
                image.save(buf, format="PNG")
                words, _ = _ocr_page(buf.getvalue())
                all_words.append(words)
                all_images.append(image)
                logger.debug("OCR page done", page=page_id + 1, words=len(words), zoom=zoom)
        return all_words, all_images

    def ocr_image(self, path: Path) -> tuple[list[list[WordDict]], list[Image.Image]]:
        logger.info("Running OCR on image", path=str(path))
        with path.open("rb") as f:
            words, img = _ocr_page(f.read())
        return [words], [img]

    def read_pdf(self, path: Path) -> list[PageRead]:
        """Every page with words from the text layer, Tesseract and RapidOCR."""
        logger.info("Reading PDF for redaction", path=str(path), dpi=settings.LOCAL_OCR_DPI)
        pages: list[PageRead] = []
        with fitz.open(str(path)) as doc:
            for page_id in range(len(doc)):
                image, zoom = _render_page(doc, page_id)
                pdf_words = _pdf_text_words(doc.load_page(page_id), zoom)
                pages.append(read_image(image, pdf_words))
        return pages

    def read_image_file(self, path: Path) -> list[PageRead]:
        logger.info("Reading image for redaction", path=str(path))
        # No EXIF transpose: previews and cloud OCR use the stored orientation,
        # and turned pages are handled by OSD.
        with Image.open(path) as source:
            image = source.convert("RGB")
        return [read_image(image)]
