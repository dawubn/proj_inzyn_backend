"""Advisory blank-field detection from Azure text and layout, independent of profiles."""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

from app.services.formal_context import MIN_WORD_CONFIDENCE, DocumentContext, OCRLine, normalize

# These labels can survive OCR even when the dotted writing area does not.
_LABELS = (
    (r"(?:z\s+)?siedzib[aą]\s+w|siedziba(?: firmy|instytucji)?(?=\s*:|\s*$)", "Siedziba"),
    (r"przy ulicy|ulica|ul\.", "Ulica"),
    (
        r"\(?nr domu\s*/\s*nr lokalu\)?|(?:nr|numer) (?:domu|lokalu|mieszkania)",
        "Numer domu / lokalu",
    ),
    (r"zamieszk\w*(?:/zamieszk\w*)?\*?\s+w", "Miejscowość zamieszkania"),
    (r"kod pocztowy", "Kod pocztowy"),
    (r"PESEL(?:\s*/\s*NIP)?(?:\)\d)?|NIP|REGON", "Identyfikator"),
    (r"\(?adres e[- ]mail\)?|e[- ]mail|telefon|tel\.", "Dane kontaktowe"),
    (r"wpisan[ay] do|nazwa rejestru", "Nazwa rejestru"),
    (r"pod numerem", "Numer w rejestrze"),
)
_LABEL_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(f"(?P<f{i}>{pattern})" for i, (pattern, _) in enumerate(_LABELS))
    + r")(?!\w)",
    re.IGNORECASE,
)
_BLANK = re.compile(r"[.\u2026_]{3,}|\u2026")
_EMPTY = re.compile(r"^[\s.,_\u2026:;()/*\-\u2013\u2014]*$")
_MASK = re.compile(r"utajn|tajniono|ajniono", re.IGNORECASE)
_GENERIC = re.compile(r"^\s*(?P<label>[^:]{2,65}):\s*(?P<value>.*)$")
_CAPTION = re.compile(
    r"^(?:nazwa miejscowosci|imie i nazwisko|miejscowosc.*data|podpis|czytelny podpis)"
)
_MAX_LABEL_WORDS = 8


def _same_row(left: OCRLine, right: OCRLine) -> bool:
    if left.page != right.page or not left.polygon or not right.polygon:
        return False
    lt, lb = min(left.polygon[1::2]), max(left.polygon[1::2])
    rt, rb = min(right.polygon[1::2]), max(right.polygon[1::2])
    return min(lb, rb) - max(lt, rt) >= min(lb - lt, rb - rt) / 3


def _rows(lines: list[OCRLine]) -> list[OCRLine]:
    # Inspect the entire row: a filled value may be far to the right of its label.
    groups: list[list[OCRLine]] = []
    fragments = [part for line in lines for part in (line.fragments or (line,))]
    for line in sorted(fragments, key=lambda item: (item.page or 0, min(item.polygon[1::2]))):
        group = next((group for group in reversed(groups) if _same_row(group[0], line)), None)
        if group is None:
            groups.append([line])
        else:
            group.append(line)
    rows = []
    for group in groups:
        group.sort(key=lambda item: min(item.polygon[::2]))
        first = group[0]
        rows.append(
            replace(first, text=" ".join(item.text for item in group), fragments=tuple(group))
        )
    return rows


def _segment(row: OCRLine, start: int, end: int) -> OCRLine:
    """Retain only original OCR fragments intersecting this label and its slot."""
    fragments = []
    offset = 0
    for part in row.fragments or (row,):
        if offset < end and offset + len(part.text) > start:
            fragments.append(part)
        offset += len(part.text) + 1
    return replace(row, text=row.text[start:end], fragments=tuple(fragments))


def _label(text: str) -> bool:
    normalized = normalize(text).strip(" ()")
    if re.match(r"^\[?\s*(?:utajn|tajniono|ajniono)", normalized):
        return False
    return bool(
        _LABEL_RE.search(normalized) or _GENERIC.match(normalized) or _CAPTION.match(normalized)
    )


def _value_below(rows: list[OCRLine], index: int) -> OCRLine | None:
    row = rows[index]
    height = max(row.polygon[1::2]) - min(row.polygon[1::2])
    for following in rows[index + 1 :]:
        gap = min(following.polygon[1::2]) - max(row.polygon[1::2])
        if following.page != row.page or gap > height:
            break
        if gap < 0 or _label(following.text):
            continue
        if max(following.polygon[::2]) < min(row.polygon[::2]):
            continue
        if min(following.polygon[::2]) > max(row.polygon[::2]):
            continue
        return following
    return None


def _blank_value(value: str) -> bool:
    # A value inserted on a dotted line is filled; trailing dots alone prove nothing.
    if _MASK.search(value):
        return False
    return bool(_EMPTY.fullmatch(value))


def _filled_caption(rows: list[OCRLine], index: int, label: str) -> bool:
    """A bare identifier/address caption can have its value ABOVE it."""
    row = rows[index]
    height = max(row.polygon[1::2]) - min(row.polygon[1::2])
    for previous in reversed(rows[:index]):
        gap = min(row.polygon[1::2]) - max(previous.polygon[1::2])
        if previous.page != row.page or gap > 2 * height:
            break
        if gap < 0 or min(previous.polygon[::2]) > max(row.polygon[::2]):
            continue
        if max(previous.polygon[::2]) < min(row.polygon[::2]):
            continue
        value = normalize(previous.text)
        if _label(value):
            return False
        categories = "pesel|nip|regon" if label == "Identyfikator" else "adres"
        if _MASK.search(value) and re.search(categories, value):
            return True
        return label == "Identyfikator" and bool(re.fullmatch(r"[\d\s-]{6,}", value))
    return False


def _finding(label: str, rows: list[OCRLine], explicit_blank: bool) -> dict[str, Any]:
    fragments = [fragment for row in rows for fragment in (row.fragments or (row,))]
    evidence = [fragment.evidence() for fragment in fragments]
    identity = repr([(e["page"], e["polygon"], e["text"]) for e in evidence]) + label
    return {
        "id": hashlib.sha256(identity.encode()).hexdigest()[:16],
        "label": label,
        "page": rows[0].page,
        "reason_code": "POSSIBLE_EMPTY_FIELD",
        "explicit_blank": explicit_blank,
        "recognized_label": True,
        "evidence": evidence,
    }


def _known_fields(
    rows: list[OCRLine], index: int, matches: list[re.Match[str]]
) -> list[dict[str, Any]]:
    row = rows[index]
    findings = []
    text = normalize(row.text)
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        value = text[match.end() : end].strip()
        next_label = re.search(r"(?<=[.…_])\s*[^:.\n]{2,65}:", value)
        if next_label:
            value = value[: next_label.start()]
        # A comma after a dotted slot starts the next clause, not its value.
        if _BLANK.match(value) and "," in value:
            value = value.split(",", 1)[0]
        if not _blank_value(value):
            continue
        explicit = bool(_BLANK.search(value))
        # Without visible dots, accept standalone labels, not mentions in prose.
        labels_only = _blank_value(_LABEL_RE.sub("", text))
        if not explicit and not labels_only and matches[0].start() != 0:
            continue
        if _CAPTION.match(text):
            continue
        following = _value_below(rows, index)
        if following and not _blank_value(following.text):
            continue
        group = match.lastgroup
        if group is None:
            continue
        label = _LABELS[int(group[1:])][1]
        if not explicit and ":" not in text and _filled_caption(rows, index, label):
            continue
        if label in {"Identyfikator", "Dane kontaktowe"}:
            label = row.text[match.start() : match.end()].strip(" ()")
            label = re.sub(r"\)\d$", "", label)
        segment = _segment(row, match.start(), end)
        findings.append(_finding(label, [segment, following] if following else [segment], explicit))
    return findings


def _generic_field(rows: list[OCRLine], index: int) -> dict[str, Any] | None:
    row = rows[index]
    generic = _GENERIC.match(row.text)
    label = ""
    evidence = [row]
    if generic and _BLANK.search(generic["value"]) and _blank_value(generic["value"]):
        following = _value_below(rows, index)
        if following and not _blank_value(following.text):
            return None
        label = generic["label"].strip()
    elif _BLANK.search(row.text) and _blank_value(row.text) and index:
        previous = rows[index - 1]
        if previous.text.rstrip().endswith(":") and _value_below(rows, index - 1) is row:
            label = previous.text.rstrip(" :")
            evidence = [previous, row]
    if not label or len(label.split()) > _MAX_LABEL_WORDS or _MASK.search(label):
        return None
    return {**_finding(label, evidence, True), "recognized_label": False}


def find_possible_empty_fields(context: DocumentContext) -> list[dict[str, Any]]:
    """Return review suggestions, never declarations of missing required data."""
    if not context.lines or any(not line.polygon for line in context.lines):
        return []
    rows = _rows(context.lines)
    findings = []
    for index, row in enumerate(rows):
        if any(
            part.confidence is not None
            and part.confidence < MIN_WORD_CONFIDENCE
            and not _blank_value(part.text)
            and not _MASK.search(part.text)
            for part in row.fragments
        ):
            continue
        matches = list(_LABEL_RE.finditer(normalize(row.text)))
        if matches:
            findings.extend(_known_fields(rows, index, matches))
        elif finding := _generic_field(rows, index):
            findings.append(finding)
    return list({item["id"]: item for item in findings}.values())
