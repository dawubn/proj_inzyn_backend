from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Collection
from statistics import median
from typing import Any

import structlog
from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

from app.enums.analysis import RedactionCategory
from app.services import redaction_ner
from app.services.redaction_rules import (
    ACCOUNT,
    EMAIL,
    PERSON,
    Span,
    find_caption_spans,
    find_pattern_spans,
    fold,
    is_label_word,
    label_selected,
    name_parts,
    name_stem,
)

DetectorFactory.seed = 0

_MIN_TEXT_LEN_FOR_NLP = 5  # skip NER on near-empty pages
_FORM_LABELS = {
    "mocodawca:",
    "pełnomocnik:",
    "pelnomocnik:",
    "petnomocnik:",  # Common Tesseract substitution of ł with t.
    "powód:",
    "powod:",
    "powódka:",
    "powodka:",
    "pozwany:",
    "pozwana:",
    "zamawiający:",
    "zamawiajacy:",
    "wykonawca:",
    "zleceniodawca:",
    "zleceniobiorca:",
    "data:",
    "podpis:",
    "adres:",
    "pesel:",
}

logger = structlog.get_logger(__name__)
_CONTINUING = (EMAIL, ACCOUNT, PERSON)

WordDict = dict[str, Any]


def _load_spacy() -> bool:
    """Kept as a single switch for language models (used by tests to disable them)."""
    return redaction_ner.models_available()


def _label(name: str) -> str:
    return f"[UTAJNIONO: {name}]"


class _PageText:
    """Text of one OCR source on a page, with a map from characters back to words."""

    def __init__(self, words: list[WordDict]) -> None:
        lines: dict[str, list[WordDict]] = defaultdict(list)
        for word in words:
            lines[str(word.get("linia", ""))].append(word)
        ordered = sorted(
            lines.values(), key=lambda ws: (min(w["y"] for w in ws), min(w["x"] for w in ws))
        )
        self.lines: list[str] = []
        self.offsets: list[tuple[int, int, WordDict]] = []
        offset = 0
        for line_words in ordered:
            parts = []
            for word in sorted(line_words, key=lambda w: w["x"]):
                self.offsets.append((offset, offset + len(word["tekst"]), word))
                parts.append(word["tekst"])
                offset += len(word["tekst"]) + 1
            self.lines.append(" ".join(parts))
        self.text = "\n".join(self.lines)

    def words_in(self, start: int, end: int) -> list[WordDict]:
        return [word for left, right, word in self.offsets if left < end and right > start]


def _detect_language(text: str) -> str:
    try:
        return "en" if detect(text) == "en" else "pl"
    except LangDetectException:
        return "pl"


def _is_label(word: WordDict) -> bool:
    text = str(word["tekst"])
    return text.casefold().strip() in _FORM_LABELS or is_label_word(text)


def _word_key(word: WordDict) -> tuple[Any, ...]:
    return word.get("zrodlo"), word["x"], word["y"], word["tekst"]


class _Collector:
    """Detected words of one page; the first label assigned to a word wins."""

    def __init__(self, words: list[WordDict]) -> None:
        heights = [w["wysokosc"] for w in words if any(c.isalnum() for c in w["tekst"])]
        self._typical_height = median(heights) if heights else 0
        self._seen: set[tuple[Any, ...]] = set()
        self.found: list[WordDict] = []

    def add(self, word: WordDict, label: str) -> None:
        key = _word_key(word)
        if key in self._seen or not any(c.isalnum() for c in word["tekst"]) or _is_label(word):
            return
        if "UTAJN" in str(word["tekst"]).upper():
            return
        self._seen.add(key)
        # Tesseract reads dotted rules as "aaa/ccc" and NLP calls them people. Still
        # cover the pixels conservatively, but do not put a presence marker on a line.
        thin_rule = (
            word["wysokosc"] < self._typical_height / 3 and word["szerokosc"] > 5 * word["wysokosc"]
        )
        self.found.append({**word, "rodzaj_danych": "" if thin_rule else _label(label)})


def _text_spans(page: _PageText, language: str | None) -> list[Span]:
    spans = find_caption_spans(page.lines) + find_pattern_spans(page.text)
    if language is not None:
        spans += redaction_ner.find_entities(page.text, language)
    return spans


def detect_document(
    pages: list[list[WordDict]], known_names: set[str] | None = None
) -> tuple[list[list[WordDict]], set[str]]:
    """Words with personal data per page and the name stems found in the document."""
    use_models = _load_spacy()
    collectors = [_Collector(words) for words in pages]
    texts: list[list[_PageText]] = []
    name_stems: set[str] = set(known_names or ())

    for words, collector in zip(pages, collectors, strict=True):
        by_source: dict[str, list[WordDict]] = defaultdict(list)
        for word in words:
            by_source[str(word.get("zrodlo", ""))].append(word)
        page_texts = [_PageText(source_words) for source_words in by_source.values()]
        texts.append(page_texts)

        joined = "\n".join(p.text for p in page_texts)
        language = None
        if use_models and len(joined.strip()) >= _MIN_TEXT_LEN_FOR_NLP:
            language = _detect_language(joined)

        for page in page_texts:
            for start, end, label in _continued(page, _text_spans(page, language)):
                if label == PERSON:
                    for part in name_parts(page.text[start:end]):
                        stem = name_stem(part)
                        if stem:
                            name_stems.add(stem)
                for word in page.words_in(start, end):
                    collector.add(word, label)

    if name_stems:
        for page_texts, collector in zip(texts, collectors, strict=True):
            for page in page_texts:
                previous = ""
                for _, _, word in page.offsets:
                    if _propagates(word["tekst"], name_stems) and not _INSTITUTION.match(
                        fold(previous)
                    ):
                        collector.add(word, PERSON)
                    previous = word["tekst"]

    return [collector.found for collector in collectors], name_stems


def find_personal_data_pages(pages: list[list[WordDict]]) -> list[list[WordDict]]:
    """Detected words per page, see detect_document."""
    return detect_document(pages)[0]


# Words after which a matching name stem is part of an institution, e.g. Uniwersytet Warszawski.
_INSTITUTION = re.compile(
    r"(?:uniwersytet|sad|sadu|urzad|urzedu|wojewodztw|politechni|akademi|szpital|starost|wojewod"
    r"|prezydent|marszal|biblioteka|muzeum|teatr|osiedl|ulic|plac|aleja|rzeczy?pospolit)\w*\W*$"
)


def _continued(page: _PageText, spans: list[Span]) -> list[Span]:
    """Values cut at the end of a line, e.g. an e-mail after "@" or a surname after "-"."""
    result = list(spans)
    for start, end, label in spans:
        if label not in _CONTINUING:
            continue
        line_end = page.text.find("\n", end)
        if line_end == -1 or page.text[end:line_end].strip():
            continue
        tail = page.text[start:end].rstrip()
        follows = page.text[line_end + 1 :].split("\n", 1)[0]
        first = re.match(r"\s*\S+", follows)
        if not first:
            continue
        cut = (
            (label == EMAIL and tail.endswith(("@", ".")))
            or (label == PERSON and tail.endswith("-"))
            or (label == ACCOUNT and re.match(r"\s*\d{2,4}\b", follows) is not None)
        )
        if cut:
            result.append((line_end + 1, line_end + 1 + first.end(), label))
    return result


_CAPITALISED = re.compile(r"^[(\"„']?[A-ZĄĆĘŁŃÓŚŹŻ]")


def _propagates(text: str, stems: set[str]) -> bool:
    if not _CAPITALISED.match(text):
        return False
    return any(name_stem(part) in stems for part in re.split(r"[\-/]", text) if part)


def select_categories(
    items: list[WordDict], categories: Collection[RedactionCategory] | None
) -> list[WordDict]:
    """Detected items of the chosen kinds of data; None keeps all of them.

    Data without a choosable kind (e.g. account or document numbers) is always kept.
    """
    if categories is None:
        return items
    return [
        item
        for item in items
        if label_selected(_kind(str(item.get("rodzaj_danych", ""))), categories)
    ]


def _kind(label: str) -> str:
    """The kind of data in a mask label, e.g. IMIE/NAZWISKO in [UTAJNIONO: IMIE/NAZWISKO]."""
    prefix = "[UTAJNIONO: "
    return label[len(prefix) : -1] if label.startswith(prefix) and label.endswith("]") else label


def find_personal_data(
    words: list[WordDict], categories: Collection[RedactionCategory] | None = None
) -> list[WordDict]:
    """Return copies of word dicts that contain sensitive data, extended with 'rodzaj_danych'.

    With `categories` only data of these kinds is returned; None means all kinds.
    """
    if categories is not None and not categories:
        return []
    return select_categories(find_personal_data_pages([words])[0], categories)
