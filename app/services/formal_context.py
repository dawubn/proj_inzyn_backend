"""Azure OCR input for formal validation. Never reads the original/local OCR."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

MIN_WORD_CONFIDENCE = 0.8
MIN_READABLE_CHARACTERS = 40


def normalize(text: str) -> str:
    return text.casefold().translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))


def _polygon(value: object) -> list[float]:
    if not isinstance(value, list):
        return []
    if value and isinstance(value[0], dict):
        return [float(point[key]) for point in value for key in ("x", "y")]
    return [float(item) for item in value if isinstance(item, int | float)]


def _overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return bool(
        max(left.get("offset", 0), right.get("offset", 0))
        < min(
            left.get("offset", 0) + left.get("length", 0),
            right.get("offset", 0) + right.get("length", 0),
        )
    )


def _line_confidence(
    page: dict[str, Any], spans: list[dict[str, Any]], polygon: list[float]
) -> float | None:
    confidences = []
    for word in page.get("words") or []:
        span = word.get("span") or {}
        belongs = any(_overlaps(span, item) for item in spans)
        word_polygon = _polygon(word.get("polygon"))
        if not spans and polygon and word_polygon:
            x = sum(word_polygon[::2]) / len(word_polygon[::2])
            y = sum(word_polygon[1::2]) / len(word_polygon[1::2])
            belongs = min(polygon[::2]) <= x <= max(polygon[::2]) and (
                min(polygon[1::2]) <= y <= max(polygon[1::2])
            )
        if belongs and isinstance(word.get("confidence"), int | float):
            confidences.append(float(word["confidence"]))
    return min(confidences) if confidences else None


@dataclass(frozen=True)
class OCRLine:
    text: str
    page: int | None
    polygon: list[float]
    spans: list[dict[str, Any]]
    confidence: float | None
    unit: str | None = None
    page_width: float | None = None
    page_height: float | None = None
    fragments: tuple[OCRLine, ...] = ()

    def evidence(self) -> dict[str, Any]:
        return {
            "source": "azure_ocr",
            "text": self.text,
            "page": self.page,
            "polygon": self.polygon,
            "spans": self.spans,
            "confidence": self.confidence,
            "unit": self.unit,
            "page_width": self.page_width,
            "page_height": self.page_height,
        }


def reading_lines(lines: list[OCRLine]) -> list[OCRLine]:
    """Join adjacent pieces of the same visual row, retaining original evidence.

    Azure sometimes emits mask labels before/after the rest of the page. Small
    labels sit at the top of the original text box, so compare vertical overlap.
    Do not join distant columns or different pages.
    """
    if not lines or any(not line.polygon for line in lines):
        return lines
    ordered = sorted(lines, key=lambda line: (line.page or 0, min(line.polygon[1::2])))
    rows: list[list[OCRLine]] = []
    for line in ordered:
        top, bottom = min(line.polygon[1::2]), max(line.polygon[1::2])
        for row in reversed(rows):
            anchor = max(row, key=lambda item: max(item.polygon[1::2]) - min(item.polygon[1::2]))
            a_top, a_bottom = min(anchor.polygon[1::2]), max(anchor.polygon[1::2])
            if anchor.page != line.page or top > a_bottom:
                continue
            overlap = min(bottom, a_bottom) - max(top, a_top)
            # Mask captions use a smaller font at the top of the text box;
            # their baseline can overlap less than half the original row.
            if overlap >= min(bottom - top, a_bottom - a_top) / 3:
                row.append(line)
                break
        else:
            rows.append([line])
    result = []
    for row in rows:
        groups: list[list[OCRLine]] = []
        for line in sorted(row, key=lambda item: min(item.polygon[::2])):
            height = max(max(item.polygon[1::2]) - min(item.polygon[1::2]) for item in row)
            if not groups or min(line.polygon[::2]) - max(groups[-1][-1].polygon[::2]) > 4 * height:
                groups.append([line])
            else:
                groups[-1].append(line)
        for group in groups:
            if len(group) == 1:
                result.append(group[0])
                continue
            left = min(min(item.polygon[::2]) for item in group)
            right = max(max(item.polygon[::2]) for item in group)
            top = min(min(item.polygon[1::2]) for item in group)
            bottom = max(max(item.polygon[1::2]) for item in group)
            first = group[0]
            confidences = [item.confidence for item in group if item.confidence is not None]
            result.append(
                OCRLine(
                    " ".join(item.text for item in group),
                    first.page,
                    [left, top, right, top, right, bottom, left, bottom],
                    [span for item in group for span in item.spans],
                    min(confidences) if confidences else None,
                    first.unit,
                    first.page_width,
                    first.page_height,
                    tuple(group),
                )
            )
    return result


@dataclass(frozen=True)
class DocumentContext:
    text: str
    lines: list[OCRLine]
    limitations: list[str]
    fingerprint: str

    @classmethod
    def from_azure(cls, raw: dict[str, Any] | None) -> DocumentContext:
        raw = raw or {}
        fingerprint = hashlib.sha256(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if "words_per_page" in raw or str(raw.get("provider", "")).startswith("local"):
            return cls("", [], ["AZURE_OCR_REQUIRED"], fingerprint)
        text = next(
            (
                raw[k]
                for k in ("content", "text_content", "full_text", "text")
                if isinstance(raw.get(k), str) and raw[k].strip()
            ),
            "",
        )
        lines: list[OCRLine] = []
        limitations: list[str] = []
        for index, page in enumerate(raw.get("pages") or []):
            page_number = int(page.get("pageNumber", page.get("page_number", index + 1)))
            page_lines = page.get("lines") or []
            if not page_lines:
                limitations.append("UNREADABLE_PAGE")
            for entry in page_lines:
                line: dict[str, Any] = {"content": entry} if isinstance(entry, str) else entry
                content = str(line.get("content", "")).strip()
                if not content:
                    continue
                spans = line.get("spans") or []
                polygon = _polygon(line.get("polygon"))
                confidence = _line_confidence(page, spans, polygon)
                if confidence is not None and confidence < MIN_WORD_CONFIDENCE:
                    limitations.append("LOW_OCR_CONFIDENCE")
                lines.append(
                    OCRLine(
                        content,
                        page_number,
                        polygon,
                        spans,
                        confidence,
                        page.get("unit"),
                        page.get("width"),
                        page.get("height"),
                    )
                )
        if not lines:
            limitations.append("LAYOUT_UNAVAILABLE")
            lines = [
                OCRLine(line.strip(), None, [], [], None)
                for line in text.splitlines()
                if line.strip()
            ]
        if not text:
            text = "\n".join(line.text for line in lines)
        if len(re.sub(r"\s", "", text)) < MIN_READABLE_CHARACTERS:
            limitations.append("INSUFFICIENT_TEXT")
        if not text.strip():
            limitations.append("NO_TEXT")
        return cls(text, lines, sorted(set(limitations)), fingerprint)
