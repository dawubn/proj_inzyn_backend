from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from itertools import pairwise
from typing import Any

from app.enums.document import DocumentType
from app.services.formal_catalog import EXTENDED_DOCUMENT_SPECS, FieldSpec
from app.services.formal_context import MIN_WORD_CONFIDENCE, DocumentContext, OCRLine, reading_lines
from app.services.formal_language import explicit_absence, has_affirmative_action

_PL_TRANSLATION = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{2,4}"
    r"|"
    r"\d{4}\s*[.\-/]\s*\d{1,2}\s*[.\-/]\s*\d{1,2}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:stycz\w*|lut\w*|mar\w*|kwiet\w*|maj(?:a|em|u|o)?|czerwc\w*|"
    r"lipc\w*|sierp\w*|wrze\w*|pa[zź]dziernik\w*|listopad\w*|grud\w*)"
    r"\s+\d{4}(?:\s*r\.?)?"
    r")\b",
    re.IGNORECASE,
)
_REDACTION_LABEL_RE = re.compile(
    r"\[?\s*(?:utajni\w*|tajniono|ajniono)\s*[:.]?\s*"
    r"(?P<label>data|adres|imi[eę](?:\s*/\s*naz\w*)?|im|"
    r"pesel|email|telefon|nip|numer dokumentu|numer rachunku)\s*\]?",
    re.IGNORECASE,
)

_CLASSIFICATION_KEYWORDS: dict[DocumentType, tuple[str, ...]] = {
    DocumentType.CONTRACT: (
        "umowa",
        "strony umowy",
        "zleceniodawca",
        "zleceniobiorca",
        "zamawiajacy",
        "wykonawca",
        "przedmiot umowy",
    ),
    DocumentType.LAWSUIT: (
        "pozew",
        "powod",
        "pozwany",
        "sad rejonowy",
        "sad okregowy",
        "wnosze o",
        "wartosc przedmiotu sporu",
        "uzasadnienie",
    ),
    DocumentType.POWER_OF_ATTORNEY: (
        "pelnomocnictwo",
        "mocodawca",
        "pelnomocnik",
        "udzielam pelnomocnictwa",
        "upowazniam",
        "do reprezentowania",
        "zakres pelnomocnictwa",
    ),
    DocumentType.INVOICE: ("faktura", "nabywca", "sprzedawca", "kwota brutto", "vat"),
    DocumentType.TAX_FORM: ("deklaracja", "pit", "urzad skarbowy", "nip", "podatek"),
}


@dataclass(frozen=True)
class FormalExtractionResult:
    document_type: DocumentType
    confidence: float
    fields: dict[str, Any]


def normalize_text(text: str) -> str:
    return text.casefold().translate(_PL_TRANSLATION)


def normalize_search_text(text: str) -> str:
    normalized = normalize_text(text)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _text_from_known_keys(raw: dict[str, Any]) -> str:
    for key in ("content", "text_content", "full_text", "text"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _text_from_words_per_page(raw: dict[str, Any]) -> str:
    words_per_page = raw.get("words_per_page")
    if not isinstance(words_per_page, list):
        return ""

    pages: list[str] = []
    for page in words_per_page:
        if not isinstance(page, list):
            continue
        words = [
            str(word.get("tekst", "")).strip()
            for word in page
            if isinstance(word, dict) and str(word.get("tekst", "")).strip()
        ]
        if words:
            pages.append(" ".join(words))
    return "\n".join(pages)


def _text_from_pages(raw: dict[str, Any]) -> str:
    pages_data = raw.get("pages")
    if not isinstance(pages_data, list):
        return ""

    lines: list[str] = []
    for page in pages_data:
        if not isinstance(page, dict):
            continue
        page_lines = page.get("lines")
        if not isinstance(page_lines, list):
            continue
        for line in page_lines:
            if isinstance(line, str) and line.strip():
                lines.append(line.strip())
            elif isinstance(line, dict) and str(line.get("content", "")).strip():
                lines.append(str(line["content"]).strip())
    return "\n".join(lines)


def extract_text_from_ocr_raw(raw: dict[str, Any] | None) -> str:
    if not raw:
        return ""

    return _text_from_known_keys(raw) or _text_from_words_per_page(raw) or _text_from_pages(raw)


def _canonical_redaction_label(label: str) -> str:
    normalized = normalize_search_text(label)
    if normalized in {"im", "imie"} or normalized.startswith("imie naz"):
        return "person"
    return normalized


def _find_redaction_labels(text: str) -> set[str]:
    return {
        _canonical_redaction_label(match.group("label"))
        for match in _REDACTION_LABEL_RE.finditer(text)
    }


def _find_date_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()
    for pattern in (_DATE_RE,):
        for match in pattern.finditer(text):
            candidate = " ".join(match.group(0).split())
            key = normalize_text(candidate)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    for match in _REDACTION_LABEL_RE.finditer(text):
        label = _canonical_redaction_label(match.group("label"))
        if label != "data":
            continue
        candidate = " ".join(match.group(0).split())
        key = normalize_text(candidate)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    tokens = normalize_search_text(keyword).split()
    token_patterns = [rf"{re.escape(token)}\w*" for token in tokens]
    pattern = r"\s+".join(token_patterns)
    return re.compile(rf"\b{pattern}\b")


EXTRACTOR_VERSION = "azure-formal-3.0"
_YEAR_DIGITS = 4
_HEADER_LAST_LINE = 5
_MIN_CLASSIFICATION_CUES = 2

# Patterns identify fields/roles at the start of a line, not mentions in body text.
_FIELD_LABELS = {
    "has_principal_section": (
        r"(?:(?:dane\s+)?(?:mocodawc[ay]|udzielajacy pelnomocnictwa)"
        r"|ja(?:,\s*nizej podpisan[ay](?:/a)?)?)"
    ),
    "has_attorney_section": (
        r"(?:(?:dane\s+)?(?:pelnomocnik[a]?|ustanawiam pelnomocnikiem)"
        r"|(?:niniejszym )?udzielam pelnomocnictwa|panu/i)"
    ),
    "has_subject_section": (
        r"(?:przedmiot(?:em)?(?: umowy)?|zakres" r" (?:umowy|prac|uslug)|obowiazki stron)"
    ),
    "has_court_section": r"(?:do\s+)?sad(?:u)? (?:rejonow\w*|okregow\w*|apelacyjn\w*)",
    "has_claim_section": (
        r"(?:zadani[ae](?: pozwu)?|wnosze o(?! przeprowadzenie dowodu| dopuszczenie dowodu)"
        r"|domagam sie)"
    ),
    "has_justification_section": r"(?:uzasadnienie|stan faktyczny)",
    "has_authorization_scope_section": (
        r"(?:zakres (?:pelnomocnictwa|umocowania)|udzielam"
        r" pelnomocnictwa(?=\s+\S)|upowazniam|upelnomocniam"
        r"|do(?=\s+reprezentowania\b|\s*:\s*$))"
    ),
    "has_attachments_section": r"(?:lista zalacznikow|zalaczniki)",
}
_ROLE_LABELS = {
    "principal": _FIELD_LABELS["has_principal_section"],
    "attorney": _FIELD_LABELS["has_attorney_section"],
    "claimant": r"(?:strona powodowa|powod(?:ka)?)(?=\s*:|\s*$)",
    "defendant": r"(?:strona pozwana|pozwany|pozwana)(?=\s*:|\s*$)",
    "client": r"(?:zamawiajacy|zleceniodawca|kupujacy|nabywca)(?=\s*:|\s*$)",
    "provider": r"(?:wykonawca|zleceniobiorca|sprzedajacy|sprzedawca)(?=\s*:|\s*$)",
}
_DATE_LABEL = (
    r"(?:data(?: (?:sporzadzenia|zawarcia|udzielenia|dokumentu))?(?=\s*:|\s*$|\s+\d|\s*\[)"
    r"|sporzadzono(?: dnia)?|zawart[ao](?: (?:w dniu|dnia))?)"
)
_ALL_LABELS = [
    *_FIELD_LABELS.values(),
    *_ROLE_LABELS.values(),
    _DATE_LABEL,
    r"\(?(?:czytelny |wlasnoreczny )?podpis(?:y|u|em)?",
    r"strony(?: umowy)?",
    r"wynagrodzenie",
    r"postanowienia\w*",
    r"imie i nazwisko\w*",
    r"(?:zamieszk\w*|legitymuj\w*|oraz numerem|wnioski dowodowe)",
    r"(?:swiadek|swiadkowie|osoba kontaktowa|sporzadzil|autor|notariusz|tlumacz|kontakt)",
]
_INLINE_LABEL = re.compile(
    r"(?<!\w)(?:mocodawca|pelnomocnik|powod(?:ka)?|pozwany|pozwana|zamawiajacy|"
    r"wykonawca|zleceniodawca|zleceniobiorca|swiadek|osoba kontaktowa|kontakt|"
    r"sporzadzil|autor|notariusz|tlumacz|"
    r"data(?: (?:sporzadzenia|zawarcia))?|zakres pelnomocnictwa|przedmiot umowy|"
    r"zadanie pozwu|uzasadnienie|podpis(?: mocodawcy)?)\s*:",
    re.IGNORECASE,
)
_PREFIX = r"^\s*(?:(?:§\s*)?\d+[.)]?\s+)?"
_PLACEHOLDER = re.compile(r"^[\s.,_\-\u2013\u2014\u2026:;\[\]()/]*$")
_MONTHS = {
    "stycznia": 1,
    "lutego": 2,
    "marca": 3,
    "kwietnia": 4,
    "maja": 5,
    "czerwca": 6,
    "lipca": 7,
    "sierpnia": 8,
    "wrzesnia": 9,
    "pazdziernika": 10,
    "listopada": 11,
    "grudnia": 12,
}


def _label_match(pattern: str, text: str) -> re.Match[str] | None:
    return re.match(_PREFIX + pattern + r"\b\s*[:\-\u2013]?\s*", normalize_text(text))


def _is_label(text: str) -> bool:
    return any(_label_match(pattern, text) for pattern in _ALL_LABELS)


def _split_labeled_rows(lines: list[OCRLine]) -> list[OCRLine]:
    """Several explicit colon labels can share one Azure line.

    Each field uses its own text while the evidence still points at the
    original row. We do not fabricate word-level coordinates for substrings.
    """
    result = []
    for line in lines:
        # Transliteration keeps character offsets; casefold() can expand names
        # such as Weiß and would shift the start of every subsequent field.
        matches = list(_INLINE_LABEL.finditer(line.text.translate(_PL_TRANSLATION)))
        boundaries = sorted({0, *(match.start() for match in matches), len(line.text)})
        for start, end in pairwise(boundaries):
            result.append(
                replace(
                    line, text=line.text[start:end].strip(), fragments=line.fragments or (line,)
                )
            )
    return result


def _empty(value: str) -> bool:
    return (
        bool(_PLACEHOLDER.fullmatch(value))
        or explicit_absence(value)
        or normalize_search_text(value)
        in {
            "do uzupelnienia",
            "wpisz dane",
            "wpisz imie i nazwisko",
            "brak",
            "nie podano",
        }
    )


def _observation(
    state: str, reason: str, lines: list[OCRLine], value: str | None = None
) -> dict[str, Any]:
    # A low-confidence candidate is not sufficient to assert presence or absence.
    fragments = [part for line in lines for part in (line.fragments or (line,))]
    if state != "masked" and any(
        line.confidence is not None
        and line.confidence < MIN_WORD_CONFIDENCE
        and not _PLACEHOLDER.fullmatch(line.text)
        and not _mask_only_fragment(line.text)
        and not re.fullmatch(r"[\[\]\s]*(?:UTAJN\w*|TUTAJN\w*)[\[\]\s]*", line.text, re.I)
        for line in fragments
    ):
        state, reason = "unknown", "LOW_OCR_CONFIDENCE"
    return {
        "state": state,
        "reason_code": reason,
        "value": value,
        "evidence": [line.evidence() for line in fragments],
    }


def _mask_only_fragment(text: str) -> bool:
    if not _REDACTION_LABEL_RE.search(text):
        return False
    remainder = _REDACTION_LABEL_RE.sub("", text)
    remainder = re.sub(r"\b(?:UTAJN\w*|TUTAJN\w*)\b", "", remainder, flags=re.I)
    return not re.search(r"[^\W\d_]{2,}", remainder)


def _best(candidates: list[dict[str, Any]], context: DocumentContext) -> dict[str, Any]:
    if not candidates:
        reason = context.limitations[0] if context.limitations else "NOT_FOUND"
        return _observation("unknown" if context.limitations else "missing", reason, [])
    states = {item["state"] for item in candidates}
    if states.intersection({"present", "masked"}) and states.intersection({"empty", "invalid"}):
        return {
            "state": "unknown",
            "reason_code": "CONFLICTING_CANDIDATES",
            "value": None,
            "evidence": [e for item in candidates for e in item["evidence"]],
        }
    priority = {"present": 0, "masked": 1, "unknown": 2, "invalid": 3, "empty": 4}
    return min(candidates, key=lambda item: priority.get(item["state"], 5))


def _block(context: DocumentContext, index: int, start: int) -> tuple[str, list[OCRLine]]:
    line = context.lines[index]
    value = line.text[start:].strip()
    evidence = [line]
    # Never consume another field, another page, or text below an explicit blank.
    if value or index + 1 >= len(context.lines):
        return value, evidence
    following = context.lines[index + 1]
    if normalize_search_text(following.text) in {"umowy", "pelnomocnictwa", "umocowania"}:
        evidence.append(following)
        if index + 2 >= len(context.lines):
            return value, evidence
        following = context.lines[index + 2]
    if following.page != line.page or _is_label(following.text):
        return value, evidence
    if line.polygon and following.polygon:
        height = max(line.polygon[1::2]) - min(line.polygon[1::2])
        gap = min(following.polygon[1::2]) - max(line.polygon[1::2])
        # Same-row pieces close enough to belong together were already joined.
        # A remaining same-row piece is a different column, not this value.
        if gap < 0 or gap > max(height * 2, 0):
            return value, evidence
    return following.text.strip(), [*evidence, following]


def _field(
    context: DocumentContext, pattern: str, min_words: int = 2, allow_redacted_context: bool = False
) -> dict[str, Any]:
    candidates = []
    scope_field = pattern == _FIELD_LABELS["has_authorization_scope_section"]
    scope_heading = r"zakres (?:pelnomocnictwa|umocowania)"
    explicit_scope = scope_field and any(
        _label_match(scope_heading, line.text) for line in context.lines
    )
    for index, line in enumerate(context.lines):
        if explicit_scope and not _label_match(scope_heading, line.text):
            continue
        match = _label_match(pattern, line.text)
        if not match:
            continue
        if scope_field and _scope_continuation(context, index):
            continue
        # A bare role above a signature box is not a second identity field.
        if (
            pattern in _ROLE_LABELS.values()
            and not line.text[match.end() :].strip()
            and _above_signature(context, index)
        ):
            continue
        value, evidence = _block(context, index, match.end())
        if (
            scope_field
            and index + 1 < len(context.lines)
            and _scope_continuation(context, index + 1)
        ):
            following = context.lines[index + 1]
            if following not in evidence:
                value += " " + following.text
                evidence.append(following)
        if pattern == _FIELD_LABELS["has_attorney_section"] and re.search(
            r"udzielam pelnomocnictwa", normalize_text(line.text)
        ):
            # The action following "do" cannot stand in for the recipient.
            value = re.split(r"\bdo\b", value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if not value:
                continue
        if not value and index + 1 < len(context.lines):
            following = context.lines[index + 1]
            if following.page == line.page and _label_match(pattern, following.text):
                continue
        state, reason = _value_state(
            value, pattern in _ROLE_LABELS.values(), min_words, allow_redacted_context
        )
        if scope_field and state == "present" and not has_affirmative_action(value):
            state, reason = "unknown", "SCOPE_NOT_ESTABLISHED"
        candidates.append(_observation(state, reason, evidence, value or None))
    return _best(candidates, context)


def _scope_continuation(context: DocumentContext, index: int) -> bool:
    if index == 0 or not _label_match(
        r"(?:do|upowazniam|upelnomocniam|udzielam pelnomocnictwa)", context.lines[index].text
    ):
        return False
    previous, current = context.lines[index - 1 : index + 1]
    match = _label_match(_FIELD_LABELS["has_authorization_scope_section"], previous.text)
    if not match or previous.page != current.page:
        return False
    previous_value = previous.text[match.end() :].strip()
    if previous_value and _empty(previous_value):
        return False
    if not previous.polygon or not current.polygon:
        return True
    height = max(previous.polygon[1::2]) - min(previous.polygon[1::2])
    gap = min(current.polygon[1::2]) - max(previous.polygon[1::2])
    return 0 <= gap <= height * 2


def _value_state(
    value: str, identity: bool, min_words: int, allow_redacted_context: bool
) -> tuple[str, str]:
    labels = _find_redaction_labels(value)
    identity_reference = identity and re.match(
        r"^(?:zgodnie z|jak wyzej|jak w zalaczniku|patrz|wedlug|dane w zalaczniku|"
        r"swiadek\b|osoba kontaktowa\b|kontakt\b)",
        normalize_text(value).strip(),
    )
    if identity and "person" in labels and not identity_reference:
        return "masked", "REDACTED_VALUE"
    if _empty(value):
        return "empty", "EMPTY_FIELD"
    if identity and (labels or identity_reference):
        return "unknown", "INSUFFICIENT_VALUE"
    if labels and not (
        allow_redacted_context
        and len(normalize_search_text(_REDACTION_LABEL_RE.sub("", value)).split()) >= min_words
    ):
        return (
            ("unknown", "INSUFFICIENT_VALUE")
            if allow_redacted_context
            else ("masked", "REDACTED_VALUE")
        )
    if len(re.findall(r"[a-zA-ZąćęłńóśźżĄĆĘŁŃÓŚŹŻ]{2,}", value)) < min_words:
        return "unknown", "INSUFFICIENT_VALUE"
    return "present", "VALUE_FOUND"


def _above_signature(context: DocumentContext, index: int) -> bool:
    line = context.lines[index]
    for following in context.lines[index + 1 : index + 4]:
        if following.page != line.page:
            break
        if not _label_match(r"(?:czytelny )?podpis(?:y|u|em)?", following.text):
            continue
        if not line.polygon or not following.polygon:
            return following == context.lines[index + 1]
        height = max(line.polygon[1::2]) - min(line.polygon[1::2])
        gap = min(following.polygon[1::2]) - max(line.polygon[1::2])
        if 0 <= gap <= 3 * height and (
            min(line.polygon[::2]) <= max(following.polygon[::2])
            and max(line.polygon[::2]) >= min(following.polygon[::2])
        ):
            return True
    return False


def _caption_value(context: DocumentContext, index: int) -> tuple[str, list[OCRLine]]:
    """A form caption describes the value ABOVE it, not the following field."""
    caption = context.lines[index]
    if not caption.polygon:
        return "", [caption]
    height = max(caption.polygon[1::2]) - min(caption.polygon[1::2])
    candidates = [
        line
        for line in context.lines[:index]
        if line.page == caption.page
        and line.polygon
        and 0 <= min(caption.polygon[1::2]) - max(line.polygon[1::2]) <= 2 * height
        and max(line.polygon[::2]) >= min(caption.polygon[::2])
        and min(line.polygon[::2]) <= max(caption.polygon[::2])
    ]
    if not candidates:
        return "", [caption]
    closest = max(candidates, key=lambda line: max(line.polygon[1::2]))
    return closest.text, [caption, closest]


def _parse_date(value: str) -> date | None:
    value = normalize_text(value).strip()
    try:
        if re.search(r"[a-z]", value):
            tokens = value.split()
            return date(int(tokens[2]), _MONTHS[tokens[1]], int(tokens[0]))
        parts = re.split(r"[.\-/]", re.sub(r"\s", "", value))
        first, second, third = map(int, parts)
        if len(parts[0]) == _YEAR_DIGITS:
            return date(first, second, third)
        if len(parts[2]) != _YEAR_DIGITS:
            return None
        return date(third, second, first)
    except (ValueError, KeyError, IndexError):
        return None


def _dates(context: DocumentContext) -> dict[str, Any]:  # noqa: PLR0912 - explicit date precedence
    explicit: list[dict[str, Any]] = []
    header: list[dict[str, Any]] = []
    for index, line in enumerate(context.lines):
        normalized = normalize_text(line.text)
        label = _label_match(_DATE_LABEL, line.text)
        caption = bool(re.fullmatch(r"miejscowosc\s*,?\s*(?:i )?data", normalized))
        if re.search(
            r"\b(?:ustaw\w*|rozporzadzen\w*|urodz\w*|termin\w*|wygasa|waznos\w*)\b", normalized
        ):
            continue
        # A date-shaped value in an identity, receipt stamp or other field
        # is not evidence of the date this document was issued.
        if (
            not label
            and not caption
            and (
                index > _HEADER_LAST_LINE
                or line.page not in (None, 1)
                or _is_label(line.text)
                or re.match(r"data\b", normalized)
                or re.search(r"\bur\.", normalized)
            )
        ):
            continue
        value, evidence = _block(context, index, label.end()) if label else (line.text, [line])
        if caption:
            value, evidence = _caption_value(context, index)
            if "," in value:
                value = value.split(",", 1)[1].strip()
        redactions = _find_redaction_labels(value)
        matches = list(_DATE_RE.finditer(value))
        candidate = None
        if "data" in redactions:
            candidate = _observation("masked", "REDACTED_DATE", evidence)
        elif matches:
            parsed = [_parse_date(match.group()) for match in matches]
            if len(set(parsed)) > 1:
                candidate = _observation("unknown", "CONFLICTING_DATES", evidence)
            elif parsed[0] is None:
                candidate = _observation("invalid", "INVALID_DATE", evidence, matches[0].group())
            else:
                candidate = _observation(
                    "present", "DOCUMENT_DATE_FOUND", evidence, parsed[0].isoformat()
                )
        elif label or caption:
            candidate = _observation(
                "empty" if _empty(value) else "unknown",
                "EMPTY_FIELD" if _empty(value) else "UNRECOGNIZED_DATE",
                evidence,
            )
        if candidate:
            (explicit if label or caption else header).append(candidate)
    candidates = explicit or header
    values = {item["value"] for item in candidates if item["state"] == "present"}
    if len(values) > 1 or any(item["reason_code"] == "CONFLICTING_DATES" for item in candidates):
        return {
            "state": "unknown",
            "reason_code": "CONFLICTING_DATES",
            "value": None,
            "evidence": [e for item in candidates for e in item["evidence"]],
        }
    return _best(candidates, context)


def _signature(context: DocumentContext) -> dict[str, Any]:
    lines = [
        line
        for line in context.lines
        if _label_match(r"\(?(?:czytelny |wlasnoreczny )?podpis(?:y|u|em)?", line.text)
    ]
    # Read OCR is not a signature detector. Blank text can have an adjacent graphical mark.
    return _observation("unknown", "SIGNATURE_REQUIRES_VISUAL_REVIEW", lines)


def _parties(context: DocumentContext, document_type: DocumentType) -> dict[str, Any]:
    pairs = (
        [("claimant", "defendant")]
        if document_type == DocumentType.LAWSUIT
        else [
            ("client", "provider"),
        ]
    )
    pairs_results = []
    for left, right in pairs:
        results = [_field(context, _ROLE_LABELS[role]) for role in (left, right)]
        states = {item["state"] for item in results}
        if states == {"present"}:
            state, reason = "present", "BOTH_PARTIES_FOUND"
        elif "empty" in states:
            state, reason = "empty", "PARTY_FIELD_EMPTY"
        elif states <= {"present", "masked"}:
            state, reason = "masked", "REDACTED_PARTY"
        elif states <= {"present", "masked", "missing"}:
            state, reason = "missing", "PARTY_NOT_FOUND"
        else:
            state, reason = "unknown", "PARTIES_UNCERTAIN"
        pairs_results.append(
            {
                "state": state,
                "reason_code": reason,
                "value": None,
                "evidence": [e for item in results for e in item["evidence"]],
            }
        )
    # An unlabelled prose relation alone does not identify either party.
    # Keep it as a candidate for review rather than accepting two arbitrary
    # words on either side (e.g. "pierwszą stroną a drugą stroną").
    for line in context.lines:
        if re.search(r"\bpomiedzy\s+\S+\s+\S+\s+(?:a|i)\s+\S+\s+\S+", normalize_text(line.text)):
            pairs_results.append(_observation("unknown", "PARTIES_UNCERTAIN", [line]))
    return _best(pairs_results, context)


_AMOUNT_VALUE = re.compile(
    r"(?:[A-Z]{3}\s*)?[-+]?(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)"
    r"(?:[.,]\d{1,2})?(?:\s*(?:[A-Z]{3}|zl))?",
    re.I,
)
_UNSTRUCTURED_TYPES = {
    DocumentType.SCIENTIFIC_ARTICLE,
    DocumentType.PATENT,
    DocumentType.MANUAL,
    DocumentType.LAW_AND_REGULATION,
    DocumentType.FINANCIAL_REPORT,
    DocumentType.GOVERNMENT_TENDER,
}


def _catalog_context(context: DocumentContext, specs: tuple[FieldSpec, ...]) -> DocumentContext:
    # Only colon-delimited labels are split within a row. Free prose must not
    # create fields just because it mentions an author, invoice or institution.
    patterns = "|".join(rf"(?:{spec.pattern})(?:\s*/\s*(?:{spec.pattern}))*" for spec in specs)
    inline = re.compile(rf"(?<!\w)(?:{patterns})\s*:", re.I)
    lines = []
    for line in context.lines:
        if not any(_catalog_match(spec, line.text) for spec in specs):
            lines.append(line)
            continue
        if any(
            spec.kind in {"section", "title", "transactions"} and _catalog_match(spec, line.text)
            for spec in specs
        ):
            lines.append(line)
            continue
        starts = sorted(
            {
                0,
                len(line.text),
                *(match.start() for match in inline.finditer(line.text.translate(_PL_TRANSLATION))),
            }
        )
        for start, end in pairwise(starts):
            text = line.text[start:end].strip()
            if text:
                lines.append(replace(line, text=text, fragments=line.fragments or (line,)))
    return replace(context, lines=lines)


def _catalog_block(
    context: DocumentContext, index: int, start: int, specs: tuple[FieldSpec, ...]
) -> tuple[str, list[OCRLine], bool]:
    line = context.lines[index]
    value, evidence = line.text[start:].strip(), [line]
    if value or index + 1 == len(context.lines):
        return value, evidence, False
    following = context.lines[index + 1]
    if following.page != line.page:
        return "", evidence, True
    if any(_catalog_match(spec, following.text) for spec in specs) or _is_label(following.text):
        return "", evidence, False
    if line.polygon and following.polygon:
        height = max(line.polygon[1::2]) - min(line.polygon[1::2])
        gap = min(following.polygon[1::2]) - max(line.polygon[1::2])
        # Dotted blanks can be far to the right. They prove an empty field,
        # unlike a distant name/number that could belong to another column.
        same_row_blank = (
            _PLACEHOLDER.fullmatch(following.text)
            and min(following.polygon[::2]) >= max(line.polygon[::2])
            and min(max(line.polygon[1::2]), max(following.polygon[1::2]))
            > max(min(line.polygon[1::2]), min(following.polygon[1::2]))
        )
        if same_row_blank:
            return following.text, [line, following], False
        if not 0 <= gap <= height * 2:
            return "", evidence, True
    return following.text.strip(), [line, following], False


def _catalog_match(spec: FieldSpec, text: str) -> re.Match[str] | None:
    # Parentheses anchor EVERY alias; bilingual captions are one label, not a
    # Polish label with an English "value" (Nazwisko / Surname).
    return _label_match(rf"(?:{spec.pattern})(?:\s*/\s*(?:{spec.pattern}))*", text)


def _catalog_date(value: str, period: bool = False) -> tuple[str, str]:
    dates = [_parse_date(match.group()) for match in _DATE_RE.finditer(value)]
    if dates:
        if None in dates:
            return "invalid", "INVALID_DATE"
        if not period and len(set(dates)) > 1:
            return "unknown", "CONFLICTING_DATES"
        return "present", "VALUE_FOUND"
    if period and re.fullmatch(r"(?:rok\s+)?\d{4}(?:\s*r\.?)?", normalize_text(value)):
        return "present", "VALUE_FOUND"
    return "unknown", "UNRECOGNIZED_DATE"


def _catalog_value(spec: FieldSpec, value: str) -> tuple[str, str]:
    labels = _find_redaction_labels(value)
    text = normalize_text(value).strip()
    if labels.intersection(spec.masks):
        return "masked", "REDACTED_VALUE"
    if spec.kind == "transactions" and re.fullmatch(
        r"(?:brak (?:operacji|transakcji)(?: w (?:tym )?okresie)?|no transactions)[.!]?", text
    ):
        return "present", "EXPLICIT_NO_TRANSACTIONS"
    if _empty(value) or re.match(r"^(?:wpisz|podaj|uzupelnij)\b", text):
        return "empty", "EMPTY_FIELD"
    if labels and spec.kind not in {"section", "text", "title", "transactions"}:
        return "unknown", "MASK_TYPE_MISMATCH"
    return _catalog_readable_value(spec, value)


def _catalog_readable_value(spec: FieldSpec, value: str) -> tuple[str, str]:
    text = normalize_text(value).strip()
    if spec.kind in {"date", "period"}:
        return _catalog_date(value, period=spec.kind == "period")
    if spec.kind in {"amount", "tax"}:
        if _AMOUNT_VALUE.fullmatch(text) or (
            spec.kind == "tax"
            and re.fullmatch(r"(?:\d{1,2}(?:[.,]\d+)?\s*%|zw\.?|zwolnion\w*|np)", text)
        ):
            return "present", "VALUE_FOUND"
        return "unknown", "UNRECOGNIZED_NUMBER"
    if spec.kind in {"identifier", "account", "pesel", "tax_id", "form_code"}:
        patterns = {
            "identifier": r"(?=.*\d)[\w ./()-]+",
            "account": r"(?:[A-Z]{2})?\d[\d A-Z-]{13,36}",
            "pesel": r"\d{11}",
            "tax_id": r"(?:\d[ -]?){10,11}",
            "form_code": r"(?:PIT|CIT|VAT|PCC|SD|AKC)[ -]?\d+[A-Z]?(?:\s*\(\d+\))?",
        }
        valid = bool(re.fullmatch(patterns[spec.kind], value, re.I))
        return ("present", "VALUE_FOUND") if valid else ("unknown", "UNRECOGNIZED_IDENTIFIER")
    if spec.key == "has_invoice_items" and re.match(
        r"^(?:ilosc|cena|wartosc netto|quantity|unit price)\b", text
    ):
        return "unknown", "TABLE_CONTENT_UNVERIFIED"
    return _value_state(
        value,
        identity=spec.kind == "identity",
        min_words=spec.min_words,
        allow_redacted_context=True,
    )


def _catalog_field(
    context: DocumentContext, spec: FieldSpec, specs: tuple[FieldSpec, ...]
) -> dict[str, Any]:
    if spec.kind in {"signature", "visual"}:
        return _observation(
            "unknown",
            (
                "SIGNATURE_REQUIRES_VISUAL_REVIEW"
                if spec.kind == "signature"
                else "VISUAL_REVIEW_REQUIRED"
            ),
            [],
        )
    candidates = []
    for index, line in enumerate(context.lines):
        match = _catalog_match(spec, line.text)
        if not match:
            if spec.kind == "form_code" and _catalog_value(spec, line.text)[0] == "present":
                candidates.append(_observation("present", "VALUE_FOUND", [line], line.text))
            continue
        value, evidence, uncertain_layout = _catalog_block(context, index, match.end(), specs)
        state, reason = _catalog_value(spec, value)
        if spec.key == "has_invoice_items" and (not value or reason == "TABLE_CONTENT_UNVERIFIED"):
            item = _invoice_description(context, line, specs)
            if item:
                value, evidence, uncertain_layout = item.text, [line, item], False
                state, reason = _catalog_value(spec, value)
            else:
                state, reason, uncertain_layout = "unknown", "TABLE_CONTENT_UNVERIFIED", False
        if uncertain_layout:
            state, reason = "unknown", "FIELD_LAYOUT_UNCERTAIN"
        candidates.append(_observation(state, reason, evidence, value or None))
    result = _best(candidates, context)
    dates = {
        _parse_date(match.group())
        for candidate in candidates
        if candidate["state"] == "present"
        for match in _DATE_RE.finditer(candidate["value"] or "")
    }
    if spec.kind == "date" and (
        len(dates) > 1 or any(c["reason_code"] == "CONFLICTING_DATES" for c in candidates)
    ):
        result = _observation("unknown", "CONFLICTING_DATES", [])
        result["evidence"] = [item for candidate in candidates for item in candidate["evidence"]]
    return result


def _invoice_description(
    context: DocumentContext, header: OCRLine, specs: tuple[FieldSpec, ...]
) -> OCRLine | None:
    """Read the nearest row under the description column, never price headers."""
    if not header.polygon:
        return None
    height = max(header.polygon[1::2]) - min(header.polygon[1::2])
    candidates = [
        line
        for line in context.lines
        if line.page == header.page
        and line.polygon
        and 0 <= min(line.polygon[1::2]) - max(header.polygon[1::2]) <= 4 * height
        and abs(min(line.polygon[::2]) - min(header.polygon[::2])) <= 2 * height
    ]
    if not candidates:
        return None
    line = min(candidates, key=lambda item: min(item.polygon[1::2]))
    if any(_catalog_match(spec, line.text) for spec in specs) or _is_label(line.text):
        return None
    return line


def _catalog_fields(context: DocumentContext, dtype: DocumentType) -> dict[str, Any]:
    document_spec = EXTENDED_DOCUMENT_SPECS.get(dtype)
    if not document_spec:
        return {}
    context = _catalog_context(context, document_spec.fields)
    results = {}
    for spec in document_spec.fields:
        result = _catalog_field(context, spec, document_spec.fields)
        if result["state"] == "missing" and (spec.advisory or dtype in _UNSTRUCTURED_TYPES):
            result.update(state="unknown", reason_code="VARIANT_FIELD_REVIEW")
        if spec.key == "has_application_date" and not result["evidence"]:
            result = _dates(context)
        results[spec.key] = result
    return results


class FormalDocumentExtractor:
    """Extract evidence-backed observations from Azure OCR of the redacted document."""

    def extract(
        self,
        raw_ocr_result: dict[str, Any] | None,
        fallback_document_type: DocumentType = DocumentType.UNKNOWN,
    ) -> FormalExtractionResult:
        context = DocumentContext.from_azure(raw_ocr_result)
        catalog_context = replace(context, lines=reading_lines(context.lines))
        context = replace(context, lines=_split_labeled_rows(catalog_context.lines))
        detected_type, confidence = self._classify(
            normalize_search_text(context.text), fallback_document_type
        )
        effective_type = (
            fallback_document_type
            if fallback_document_type != DocumentType.UNKNOWN
            else detected_type
        )
        observations = {
            field: _field(
                context,
                pattern,
                min_words=1 if field == "has_court_section" else 2,
                allow_redacted_context=field
                in {
                    "has_subject_section",
                    "has_claim_section",
                    "has_justification_section",
                    "has_authorization_scope_section",
                },
            )
            for field, pattern in _FIELD_LABELS.items()
        }
        attachments = observations["has_attachments_section"]
        if not attachments["evidence"]:
            attached_page = _field(context, r"zalacznik nr \d+")
            if attached_page["state"] in {"present", "masked"}:
                observations["has_attachments_section"] = attachments = attached_page
        if not attachments["evidence"]:
            if re.search(r"\bzalacznik\w*", normalize_text(context.text)):
                attachments.update(state="unknown", reason_code="ATTACHMENT_CONTENT_UNVERIFIED")
            elif not context.limitations:
                attachments.update(state="not_applicable", reason_code="NO_ATTACHMENT_REQUIREMENT")
        observations["has_date"] = _dates(context)
        observations["has_signature"] = _signature(context)
        observations["has_parties_section"] = _parties(context, effective_type)
        observations["has_text"] = _observation(
            "present" if context.text.strip() else "unknown",
            "TEXT_FOUND" if context.text.strip() else "NO_TEXT",
            [],
        )
        observations.update(_catalog_fields(catalog_context, effective_type))
        fields: dict[str, Any] = {
            "document_text": context.text,
            "observations": observations,
            "extraction_version": EXTRACTOR_VERSION,
            "input_fingerprint": context.fingerprint,
            "ocr_limitations": context.limitations,
            "date_candidates": _find_date_candidates(context.text)[:5],
            "redaction_labels": sorted(_find_redaction_labels(context.text)),
            "detected_sections": sorted(
                key
                for key, item in observations.items()
                if item["state"] == "present" and key.endswith("section")
            ),
        }
        fields.update(
            {key: item["state"] in {"present", "masked"} for key, item in observations.items()}
        )
        return FormalExtractionResult(detected_type, confidence, fields)

    def _classify(
        self, search_text: str, fallback_document_type: DocumentType
    ) -> tuple[DocumentType, float]:
        scores = {
            doc_type: sum(
                bool(_keyword_pattern(keyword).search(search_text)) for keyword in keywords
            )
            for doc_type, keywords in _CLASSIFICATION_KEYWORDS.items()
        }
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_type, score = ranked[0]
        # Heuristics only recover an unavailable ML classifier when several independent cues agree.
        if score < _MIN_CLASSIFICATION_CUES or score == ranked[1][1]:
            return fallback_document_type, 0.0
        confidence = round(score / len(_CLASSIFICATION_KEYWORDS[best_type]), 4)
        return best_type, confidence
