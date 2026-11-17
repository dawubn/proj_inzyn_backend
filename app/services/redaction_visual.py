"""Signatures, unread handwriting in personal fields, faces and codes found on the image."""

from __future__ import annotations

import re
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
import structlog
from PIL import Image

from app.core.config import settings
from app.services.redaction_rules import CODE, PHOTO, SIGNATURE, caption_label, fold

logger = structlog.get_logger(__name__)

WordDict = dict[str, Any]
Box = tuple[int, int, int, int]

_SIGNATURE_CAPTION = re.compile(
    r"^\(?(?:czytelny |wlasnoreczny )?podpis\w*|^\(?signature|^signed|^sign here|^\(?parafa"
    r"|^poważaniem|^powazaniem|^szacunku|^sincerely|^faithfully|^regards"
)
_ROLE_CAPTION = re.compile(
    r"^(?:wynajmujac[ya]|najemca|mocodawca|pelnomocnik|zamawiajac[ya]|wykonawca|zleceniodawca"
    r"|zleceniobiorca|sprzedajac[ya]|kupujac[ya]|sprzedawca|nabywca|pracodawca|pracownik"
    r"|darczyn(?:ca|czyni)|obdarowan[ya]|wnioskodawca|powod|pozwan[ya]|poreczyciel"
    r"|landlord|tenant|seller|buyer|employer|employee|claimant|defendant|principal|agent)[:.]?$"
)
_FIELD_CAPTION = re.compile(r"[:]$")
_DOTS = re.compile(r"[.…_·]{3,}")
_INK_LEVEL = 170
_COLOR_SATURATION = 45
_PAPER_LEVEL = 235
# Sizes in multiples of the median word height on the page.
_MIN_STROKE = 0.25
_SPECK = 0.15
_SEED_HEIGHT = 1.3
_SEED_WIDTH = 2.5
_GROW_GAP = 0.6
_STROKE_DILATION = 0.35
_MAX_SIGNATURE_WIDTH = 30
_MAX_SIGNATURE_HEIGHT = 8
_MAX_STROKE_FILL = 0.25
_LOWER_PART = 0.5
_MAX_PHOTO_SHARE = 0.25
_MIN_PHOTO_FILL = 0.6


def _box(word: WordDict) -> Box:
    return word["x"], word["y"], word["x"] + word["szerokosc"], word["y"] + word["wysokosc"]


def _inside(inner: Box, outer: Box, share: float) -> bool:
    w = min(inner[2], outer[2]) - max(inner[0], outer[0])
    h = min(inner[3], outer[3]) - max(inner[1], outer[1])
    if w <= 0 or h <= 0:
        return False
    area = max(1, (inner[2] - inner[0]) * (inner[3] - inner[1]))
    return w * h / area >= share


def _region(box: Box, label: str) -> WordDict:
    return {
        "x": box[0],
        "y": box[1],
        "szerokosc": box[2] - box[0],
        "wysokosc": box[3] - box[1],
        "rodzaj_danych": f"[UTAJNIONO: {label}]",
    }


def _ink_mask(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
    dark = rgb.min(axis=2) < _INK_LEVEL
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    colored = (spread > _COLOR_SATURATION) & (rgb.max(axis=2) < _PAPER_LEVEL)
    return dark | colored


class _Ink:
    """Connected ink components of the whole page, so no stroke is cut at a zone edge."""

    def __init__(self, image: Image.Image) -> None:
        import cv2

        mask = _ink_mask(image)
        rgb = np.asarray(image.convert("RGB"), dtype=np.int16)
        count, self.labels, stats, _ = cv2.connectedComponentsWithStats(
            mask.astype(np.uint8), connectivity=8
        )
        self.ids = np.arange(1, count)
        self.x0, self.y0 = stats[1:, 0], stats[1:, 1]
        self.x1, self.y1 = self.x0 + stats[1:, 2], self.y0 + stats[1:, 3]
        self.area = stats[1:, 4]
        spread = rgb.max(axis=2) - rgb.min(axis=2)
        colored = np.bincount(
            np.asarray(self.labels, dtype=np.int64).ravel(),
            weights=(spread > _COLOR_SATURATION).ravel(),
            minlength=count,
        )
        self.colored = colored[1:] > 0.5 * self.area

    def box(self, index: int) -> Box:
        return int(self.x0[index]), int(self.y0[index]), int(self.x1[index]), int(self.y1[index])

    def touching(self, zone: Box) -> list[int]:
        x0, y0, x1, y1 = zone
        hits = (self.x0 < x1) & (self.x1 > x0) & (self.y0 < y1) & (self.y1 > y0)
        return [int(i) for i in np.nonzero(hits)[0]]

    def pixels(self, indexes: list[int], box: Box, h: float) -> np.ndarray:
        """Thickened strokes of the chosen components, cropped to the box."""
        import cv2

        x0, y0, x1, y1 = box
        crop = np.isin(self.labels[y0:y1, x0:x1], self.ids[indexes]).astype(np.uint8)
        size = max(3, int(_STROKE_DILATION * h)) | 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        return cv2.dilate(crop, kernel).astype(bool)


def _similar(a: str, b: str) -> bool:
    from difflib import SequenceMatcher

    left, right = fold(a).strip(".,:;()"), fold(b).strip(".,:;()")
    if not left or not right:
        return False
    return left == right or SequenceMatcher(None, left, right).ratio() >= 0.75


def _printed_boxes(words: list[WordDict], line_height: float) -> list[Box]:
    """Words that are surely print: PDF text layer or the same word read by both engines."""
    printed = [_box(w) for w in words if w.get("zrodlo") == "pdf_text"]
    rapid = [w for w in words if w.get("zrodlo") == "rapidocr"]
    for word in words:
        if word.get("zrodlo") != "tesseract" or word["wysokosc"] > 1.35 * line_height:
            continue
        box = _box(word)
        for other in rapid:
            other_box = _box(other)
            overlap = _inside(box, other_box, 0.4) or _inside(other_box, box, 0.4)
            short = len(word["tekst"]) <= 2 and len(other["tekst"]) <= 2
            if overlap and (
                short
                or _similar(word["tekst"], other["tekst"])
                or fold(word["tekst"]) in fold(other["tekst"])
            ):
                printed.append(box)
                break
    return printed


def _handwriting(ink: _Ink, indexes: list[int], printed: list[Box], h: float) -> list[int]:
    """Components that are not print: no dots, rules, frames or letters of printed words."""
    kept = []
    for index in indexes:
        box = ink.box(index)
        w, hh = box[2] - box[0], box[3] - box[1]
        if hh < _MIN_STROKE * h or (w < _SPECK * h and hh < _SPECK * h):
            continue  # dotted guides, underlines and specks
        if w > _MAX_SIGNATURE_WIDTH * h or hh > _MAX_SIGNATURE_HEIGHT * h:
            continue  # table grids and frames span much more than one signature
        if any(_inside(box, p, 0.5) for p in printed):
            continue
        kept.append(index)
    return kept


def _is_seed(ink: _Ink, index: int, h: float) -> bool:
    x0, y0, x1, y1 = ink.box(index)
    return (y1 - y0) >= _SEED_HEIGHT * h or (x1 - x0) >= _SEED_WIDTH * h


def _grow(ink: _Ink, seeds: list[int], candidates: list[int], h: float) -> list[int]:
    """Seed strokes plus the smaller strokes next to them, e.g. the dot of an initial."""
    group = list(seeds)
    rest = [i for i in candidates if i not in seeds]
    added = True
    while added:
        added = False
        x0, y0, x1, y1 = _union([ink.box(i) for i in group], int(_GROW_GAP * h))
        for index in list(rest):
            bx0, by0, bx1, by1 = ink.box(index)
            if bx0 < x1 and bx1 > x0 and by0 < y1 and by1 > y0:
                group.append(index)
                rest.remove(index)
                added = True
    return group


def _shape(
    ink: _Ink, indexes: list[int], size: tuple[int, int], h: float, printed: list[Box]
) -> WordDict:
    box = _clip(_union([ink.box(i) for i in indexes], int(_STROKE_DILATION * h)), *size)
    region = _region(box, SIGNATURE)
    pixels = ink.pixels(indexes, box, h)
    # A stroke touching a printed line above must not take those words with it.
    for px0, py0, px1, py1 in printed:
        left, top = max(px0, box[0]), max(py0, box[1])
        right, bottom = min(px1, box[2]), min(py1, box[3])
        if right > left and bottom > top:
            pixels[top - box[1] : bottom - box[1], left - box[0] : right - box[0]] = False
    region["piksele"] = pixels
    return region


def _union(boxes: list[Box], pad: int) -> Box:
    return (
        min(b[0] for b in boxes) - pad,
        min(b[1] for b in boxes) - pad,
        max(b[2] for b in boxes) + pad,
        max(b[3] for b in boxes) + pad,
    )


def _clip(box: Box, width: int, height: int) -> Box:
    return max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3])


def _caption_words(words: list[WordDict]) -> list[WordDict]:
    seen: list[Box] = []
    result = []
    for word in words:
        if any(_inside(_box(word), b, 0.5) for b in seen):
            continue
        seen.append(_box(word))
        result.append(word)
    return result


def _same_line_end(word: WordDict, words: list[WordDict], width: int, line_height: float) -> int:
    """Right edge of the field: the next caption on the same line or the page margin."""
    start = word["x"] + word["szerokosc"]
    y_mid = word["y"] + word["wysokosc"] / 2
    right = width
    for other in words:
        if other["x"] <= start or not re.search(r"\w{2,}:$", other["tekst"]):
            continue
        if abs(other["y"] + other["wysokosc"] / 2 - y_mid) > line_height:
            continue
        right = min(right, other["x"] - int(0.2 * line_height))
    return right


def _caption_box(caption: WordDict, words: list[WordDict], h: float) -> Box:
    """A caption with the words completing it, e.g. "Podpis" + "mocodawcy:"."""
    box = _box(caption)
    if caption["tekst"].endswith(":"):
        return box
    following = sorted(
        (
            w
            for w in words
            if w.get("zrodlo") == caption.get("zrodlo")
            and w.get("linia") == caption.get("linia")
            and w["x"] > caption["x"]
        ),
        key=lambda w: w["x"],
    )
    for word in following[:3]:
        if word["x"] - box[2] > 1.5 * h:
            break
        box = (box[0], min(box[1], word["y"]), _box(word)[2], max(box[3], _box(word)[3]))
        if word["tekst"].endswith(":"):
            break
    return box


def _signature_captions(words: list[WordDict], h: float) -> list[WordDict]:
    bottom = max(w["y"] + w["wysokosc"] for w in words)
    captions = []
    for word in _caption_words(words):
        folded = fold(word["tekst"])
        if _SIGNATURE_CAPTION.match(folded):
            captions.append(word)
        elif _ROLE_CAPTION.match(folded) and word["y"] >= bottom - 6 * h:
            middle = word["y"] + word["wysokosc"] / 2
            neighbours = [
                w
                for w in words
                if w is not word
                and w.get("zrodlo") == word.get("zrodlo")
                and w["wysokosc"] <= 1.35 * h
                and abs(w["y"] + w["wysokosc"] / 2 - middle) < 0.6 * h
            ]
            if all(_ROLE_CAPTION.match(fold(w["tekst"])) for w in neighbours):
                captions.append(word)
    return captions


def _signatures(
    image: Image.Image, words: list[WordDict], ink: _Ink, printed: list[Box], h: float
) -> list[WordDict]:
    width, height = image.size
    regions = []
    for caption in _signature_captions(words, h):
        x0, y0, x1, y1 = _caption_box(caption, words, h)
        end_word = {**caption, "x": x0, "szerokosc": x1 - x0}
        right = _same_line_end(end_word, words, width, h)
        zones = [
            (x1, int(y0 - 1.0 * h), min(right, int(x1 + 16 * h)), int(y1 + 1.2 * h)),
            (int(x0 - 2 * h), int(y0 - 3.5 * h), int(x1 + 6 * h), y0),
            (int(x0 - 1 * h), y1, int(x0 + 12 * h), int(y1 + 3.2 * h)),
        ]
        for raw_zone in zones:
            zone = _clip(raw_zone, width, height)
            strokes = [
                i
                for i in _handwriting(ink, ink.touching(zone), printed, h)
                if not _inside(ink.box(i), (x0, y0, x1, y1), 0.3)
            ]
            seeds = [i for i in strokes if _is_seed(ink, i, h)]
            if seeds:
                group = _grow(ink, seeds, strokes, h)
                regions.append(_shape(ink, group, (width, height), h, printed))
    return regions


def _loose_signatures(
    image: Image.Image, words: list[WordDict], ink: _Ink, printed: list[Box], h: float
) -> list[WordDict]:
    """Signatures without a caption, e.g. over a typed name at the end of a letter."""
    width, height = image.size
    top = min(w["y"] for w in words)
    bottom = max(w["y"] + w["wysokosc"] for w in words)
    lower = top + (bottom - top) * _LOWER_PART
    zone = _clip((0, int(lower), width, int(bottom + 3 * h)), width, height)
    strokes = _handwriting(ink, ink.touching(zone), printed, h)
    noise = [
        _box(w)
        for w in words
        if w.get("zrodlo") != "pdf_text" and not any(_inside(_box(w), p, 0.4) for p in printed)
    ]
    seeds = []
    for index in strokes:
        box = ink.box(index)
        area = max(1, (box[2] - box[0]) * (box[3] - box[1]))
        sparse = ink.area[index] / area <= _MAX_STROKE_FILL
        unread = ink.colored[index] or any(
            _inside(n, box, 0.2) or _inside(box, n, 0.2) for n in noise
        )
        if _is_seed(ink, index, h) and sparse and unread:
            seeds.append(index)
    regions = []
    while seeds:
        group = _grow(ink, [seeds[0]], strokes, h)
        seeds = [i for i in seeds if i not in group]
        regions.append(_shape(ink, group, (width, height), h, printed))
    return regions


def _unread_fields(
    image: Image.Image, words: list[WordDict], ink: _Ink, printed: list[Box], h: float
) -> list[WordDict]:
    """Ink after a personal field caption that no engine could read, e.g. real handwriting."""
    width, height = image.size
    read = [_box(w) for w in words]
    regions = []
    for word in _caption_words(words):
        if not (_FIELD_CAPTION.search(word["tekst"]) or _before_dots(word, words, h)):
            continue
        label = caption_label(_caption_text(word, words, h))
        if label is None:
            continue
        _, y0, x1, y1 = _box(word)
        right = min(_same_line_end(word, words, width, h), _next_printed(word, printed, width, h))
        zone = _clip((x1, int(y0 - 0.6 * h), right, int(y1 + 0.6 * h)), width, height)
        strokes = [
            ink.box(i)
            for i in _handwriting(ink, ink.touching(zone), printed, h)
            if not any(_inside(ink.box(i), r, 0.5) for r in read)
        ]
        if strokes:
            regions.append(_region(_clip(_union(strokes, int(0.15 * h)), width, height), label))
    return regions


def _before_dots(word: WordDict, words: list[WordDict], h: float) -> bool:
    """A caption followed by a dotted rule, e.g. "Adres ......", in forms without colons."""
    right = word["x"] + word["szerokosc"]
    middle = word["y"] + word["wysokosc"] / 2
    return any(
        _DOTS.fullmatch(other["tekst"])
        and 0 <= other["x"] - right < h
        and abs(other["y"] + other["wysokosc"] / 2 - middle) < h
        for other in words
    )


def _next_printed(word: WordDict, printed: list[Box], width: int, h: float) -> int:
    """Left edge of the first printed word after the field, which ends the value."""
    y_mid = word["y"] + word["wysokosc"] / 2
    start = word["x"] + word["szerokosc"] + h
    candidates = [
        b[0] for b in printed if b[0] > start and abs((b[1] + b[3]) / 2 - y_mid) < 0.6 * h
    ]
    return int(min(candidates) - 0.2 * h) if candidates else width


def _caption_text(word: WordDict, words: list[WordDict], h: float) -> str:
    """The caption ending with this word, e.g. "Adres zamieszkania:"."""
    line = sorted(
        (
            w
            for w in words
            if w.get("zrodlo") == word.get("zrodlo")
            and w.get("linia") == word.get("linia")
            and w["x"] <= word["x"]
            and word["x"] - w["x"] < 12 * h
        ),
        key=lambda w: w["x"],
    )
    return " ".join(w["tekst"] for w in line[-4:]).rstrip(":")


_face_detector: Any = None
_face_loaded = False


def _faces(image: Image.Image) -> list[WordDict]:
    global _face_detector, _face_loaded  # noqa: PLW0603
    import cv2

    if not _face_loaded:
        _face_loaded = True
        model = Path(settings.REDACTION_MODELS_DIR) / "face_detection_yunet_2023mar.onnx"
        if model.exists():
            _face_detector = cv2.FaceDetectorYN.create(str(model), "", (320, 320), 0.8)
        else:
            logger.warning("Face detection model not found", path=str(model))
    if _face_detector is None:
        return []

    scale = min(1.0, 1280 / max(image.size))
    small = image.convert("RGB").resize(
        (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    )
    bgr = np.asarray(small)[:, :, ::-1].copy()
    _face_detector.setInputSize((bgr.shape[1], bgr.shape[0]))
    _, faces = _face_detector.detect(bgr)
    regions = []
    for face in faces if faces is not None else []:
        x, y, w, h = (float(v) / scale for v in face[:4])
        # The whole ID photo, with hair and shoulders, not only the face.
        box = (int(x - 1.0 * w), int(y - 1.1 * h), int(x + 2.0 * w), int(y + 2.6 * h))
        box = _photo_frame(image, box) or box
        regions.append(_region(_clip(box, image.width, image.height), PHOTO))
    return regions


def _photo_frame(image: Image.Image, face: Box) -> Box | None:
    """The picture the face sits in: the non-paper area around it, if it is not the page."""
    import cv2

    gray = np.asarray(image.convert("L"))
    closed = cv2.morphologyEx(
        (gray < _PAPER_LEVEL).astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)
    cx = min(gray.shape[1] - 1, max(0, (face[0] + face[2]) // 2))
    cy = min(gray.shape[0] - 1, max(0, (face[1] + face[3]) // 2))
    label = labels[cy, cx]
    if label == 0 or label >= count:
        return None
    x, y, w, h, area = stats[label]
    if w * h > _MAX_PHOTO_SHARE * gray.size:
        return None  # the whole page is a photo, keep the face box
    if area < _MIN_PHOTO_FILL * w * h:
        return None  # joined with text next to it, not a picture
    return (
        min(face[0], int(x)) - 6,
        min(face[1], int(y)) - 6,
        max(face[2], int(x + w)) + 6,
        max(face[3], int(y + h)) + 6,
    )


def _codes(image: Image.Image) -> list[WordDict]:
    import zxingcpp

    regions = []
    for code in zxingcpp.read_barcodes(image.convert("L")):
        pos = code.position
        points = [pos.top_left, pos.top_right, pos.bottom_right, pos.bottom_left]
        xs, ys = [p.x for p in points], [p.y for p in points]
        box = (min(xs) - 12, min(ys) - 12, max(xs) + 12, max(ys) + 12)
        regions.append(_region(_clip(box, image.width, image.height), CODE))
    return regions


def _join_shapes(regions: list[WordDict]) -> list[WordDict]:
    """Overlapping signature shapes found from different captions become one region."""
    joined: list[WordDict] = []
    for region in regions:
        for other in joined:
            if _inside(_region_box(region), _region_box(other), 0.05) or _inside(
                _region_box(other), _region_box(region), 0.05
            ):
                box = _union([_region_box(region), _region_box(other)], 0)
                canvas = np.zeros((box[3] - box[1], box[2] - box[0]), dtype=bool)
                for part in (region, other):
                    y, x = part["y"] - box[1], part["x"] - box[0]
                    shape = part["piksele"]
                    canvas[y : y + shape.shape[0], x : x + shape.shape[1]] |= shape
                other.update(_region(box, SIGNATURE))
                other["piksele"] = canvas
                break
        else:
            joined.append(region)
    return joined


def _region_box(region: WordDict) -> Box:
    return (
        region["x"],
        region["y"],
        region["x"] + region["szerokosc"],
        region["y"] + region["wysokosc"],
    )


def find_visual_regions(image: Image.Image, words: list[WordDict]) -> list[WordDict]:
    """Regions to mask that are not tied to recognised words."""
    regions: list[WordDict] = []
    # Each check only adds masks; one failing must not switch off the others.
    for detector in (_faces, _codes):
        try:
            regions += detector(image)
        except Exception:
            logger.exception("Visual redaction check failed", check=detector.__name__)
    heights = [w["wysokosc"] for w in words if re.search(r"[^\W\d_]{2,}", w["tekst"])]
    if not heights:
        return regions
    line_height = float(median(heights))
    ink = _Ink(image)
    printed = _printed_boxes(words, line_height)
    shapes: list[WordDict] = []
    for finder in (_signatures, _loose_signatures):
        try:
            shapes += finder(image, words, ink, printed, line_height)
        except Exception:
            logger.exception("Visual redaction check failed", check=finder.__name__)
    regions += _join_shapes(shapes)
    try:
        regions += _unread_fields(image, words, ink, printed, line_height)
    except Exception:
        logger.exception("Visual redaction check failed", check="_unread_fields")
    return regions


def ink_regions(image: Image.Image, words: list[WordDict], boxes: list[WordDict]) -> list[WordDict]:
    """Ink inside the boxes without the printed words around it."""
    heights = [w["wysokosc"] for w in words if re.search(r"[^\W\d_]{2,}", w["tekst"])]
    if not boxes or not heights:
        return []
    import cv2

    line_height = float(median(heights))
    mask = _ink_mask(image)
    printed = _printed_boxes(words, line_height)
    size = max(3, int(_STROKE_DILATION * line_height)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    regions = []
    for item in boxes:
        box = _clip(
            (item["x"], item["y"], item["x"] + item["szerokosc"], item["y"] + item["wysokosc"]),
            image.width,
            image.height,
        )
        crop = mask[box[1] : box[3], box[0] : box[2]].copy()
        for px0, py0, px1, py1 in printed:
            left, top = max(px0, box[0]), max(py0, box[1])
            right, bottom = min(px1, box[2]), min(py1, box[3])
            if right > left and bottom > top:
                crop[top - box[1] : bottom - box[1], left - box[0] : right - box[0]] = False
        region = _region(box, SIGNATURE)
        region["piksele"] = cv2.dilate(crop.astype(np.uint8), kernel).astype(bool)
        regions.append(region)
    return regions
