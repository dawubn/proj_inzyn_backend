"""
Locates formal validation issues on the document pages for the analysis viewer.

Formal issues report missing elements, so there is no offending text to highlight. Instead
each issue gets the place where the element should be:

- "placeholder": an unfilled template field found in the text (e.g. "..., dnia ......"),
- "expected": the conventional spot in Polish legal documents (e.g. signature below the
  last line of text), used when no placeholder exists,
- page only (no rects): sections whose position cannot be reasonably guessed.

Rects are relative to the page (0-1), like the locations of legal analysis errors.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from typing import Any, Literal

from app.schemas.analysis_report import ValidationIssue

LocationKind = Literal["placeholder", "expected", "page"]

_TOKEN_RE = re.compile(r"\w+")

# Lines containing these token sequences are unfilled fields for the element
# A template label ("(miejscowość i data)") marks the field by itself; common words like "dnia"
# only when the line also has a blank to fill in ("..., dnia ......")
_PLACEHOLDER_CUES: dict[str, tuple[tuple[tuple[str, ...], bool], ...]] = {
    "has_date": (
        (("miejscowosc", "i", "data"), False),
        (("miejscowość", "i", "data"), False),
        (("dnia",), True),
        (("data",), True),
    ),
}
_BLANK_RE = re.compile(r"[.…_]{3,}|…")

# Sections conventionally found on the first page
_FIRST_PAGE_FIELDS = frozenset(
    {
        "has_parties_section",
        "has_subject_section",
        "has_court_section",
        "has_claim_section",
        "has_principal_section",
        "has_attorney_section",
        "has_authorization_scope_section",
    }
)

_DATE_REGION = {"x": 0.55, "y": 0.04, "width": 0.37, "height": 0.06}
_END_BAND_HEIGHT = 0.06
_END_BAND_GAP = 0.015
_MAX_BAND_TOP = 1 - _END_BAND_HEIGHT - 0.02


def locate_formal_issues(
    issues: list[ValidationIssue], tesseract_words: list[dict[str, Any]]
) -> list[ValidationIssue]:
    """Return the issues with `details["location"]` set wherever a location can be derived."""
    pages = _Pages(tesseract_words)
    if not pages.has_text:
        return issues

    missing = {issue.field_name for issue in issues}
    return [
        (
            issue.model_copy(update={"details": {**issue.details, "location": location}})
            if (location := _locate(issue.field_name, pages, missing)) is not None
            else issue
        )
        for issue in issues
    ]


def _locate_date(pages: _Pages, _missing: set[str | None]) -> dict[str, Any]:
    placeholder = pages.find_line(_PLACEHOLDER_CUES["has_date"])
    if placeholder:
        page, rect = placeholder
        return _location("placeholder", page, [rect])
    return _location("expected", 1, [_DATE_REGION])


def _locate_signature(pages: _Pages, _missing: set[str | None]) -> dict[str, Any]:
    page, top = pages.end_of_text()
    rect = {"x": 0.55, "y": top, "width": 0.37, "height": _END_BAND_HEIGHT}
    return _location("expected", page, [rect])


def _locate_attachments(pages: _Pages, missing: set[str | None]) -> dict[str, Any]:
    page, top = pages.end_of_text()
    if "has_signature" in missing:
        # Attachments are listed below the (missing) signature
        top = min(top + _END_BAND_HEIGHT + _END_BAND_GAP, _MAX_BAND_TOP)
    rect = {"x": 0.08, "y": top, "width": 0.45, "height": _END_BAND_HEIGHT}
    return _location("expected", page, [rect])


def _locate_justification(pages: _Pages, _missing: set[str | None]) -> dict[str, Any]:
    page, _ = pages.end_of_text()
    return _location("page", page, [])


_LOCATORS: dict[str, Callable[[_Pages, set[str | None]], dict[str, Any]]] = {
    "has_date": _locate_date,
    "has_signature": _locate_signature,
    "has_attachments_section": _locate_attachments,
    "has_justification_section": _locate_justification,
}


def _locate(
    field_name: str | None, pages: _Pages, missing: set[str | None]
) -> dict[str, Any] | None:
    if field_name in _FIRST_PAGE_FIELDS:
        return _location("page", 1, [])
    locator = _LOCATORS.get(field_name or "")
    return locator(pages, missing) if locator else None


def _location(kind: LocationKind, page: int, rects: list[dict[str, float]]) -> dict[str, Any]:
    return {
        "kind": kind,
        "page": page,
        "rects": [{key: round(value, 5) for key, value in rect.items()} for rect in rects],
    }


class _Pages:
    """Tesseract words with normalized boxes, grouped into pages and lines."""

    def __init__(self, words: list[dict[str, Any]]) -> None:
        self._lines: dict[int, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for word in words:
            # Dot runs like "......," are kept: they are the blanks of template fields
            if "nbbox" in word and word["text"].strip():
                self._lines[word["page"]][str(word.get("line"))].append(word)

    @property
    def has_text(self) -> bool:
        return bool(self._lines)

    def find_line(
        self, cues: tuple[tuple[tuple[str, ...], bool], ...]
    ) -> tuple[int, dict[str, float]] | None:
        """First line (in cue priority, then reading order) containing one of the cues."""
        for cue, needs_blank in cues:
            for page in sorted(self._lines):
                for line_words in self._lines_in_reading_order(page):
                    tokens = [t for w in line_words for t in _TOKEN_RE.findall(w["text"].lower())]
                    if not _contains_sequence(tokens, cue):
                        continue
                    if needs_blank and not any(_BLANK_RE.search(w["text"]) for w in line_words):
                        continue
                    return page, _union([w["nbbox"] for w in line_words])
        return None

    def end_of_text(self) -> tuple[int, float]:
        """Last page with text and the top of a band just below its last line."""
        page = max(self._lines)
        bottom = max(
            w["nbbox"]["y"] + w["nbbox"]["height"]
            for line_words in self._lines[page].values()
            for w in line_words
        )
        return page, min(bottom + _END_BAND_GAP, _MAX_BAND_TOP)

    def _lines_in_reading_order(self, page: int) -> list[list[dict[str, Any]]]:
        return sorted(
            self._lines[page].values(),
            key=lambda line_words: min(w["nbbox"]["y"] for w in line_words),
        )


def _contains_sequence(tokens: list[str], cue: tuple[str, ...]) -> bool:
    size = len(cue)
    return any(tuple(tokens[i : i + size]) == cue for i in range(len(tokens) - size + 1))


def _union(boxes: list[dict[str, float]]) -> dict[str, float]:
    x0 = min(box["x"] for box in boxes)
    y0 = min(box["y"] for box in boxes)
    x1 = max(box["x"] + box["width"] for box in boxes)
    y1 = max(box["y"] + box["height"] for box in boxes)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}
