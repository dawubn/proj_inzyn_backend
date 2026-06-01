from __future__ import annotations

import re
from typing import Any

import spacy
import structlog
from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

DetectorFactory.seed = 0

_MIN_TEXT_LEN_FOR_NLP = 5  # skip spaCy on near-empty pages

logger = structlog.get_logger(__name__)

WordDict = dict[str, Any]

try:
    _nlp_pl = spacy.load("pl_core_news_lg")
    _nlp_en = spacy.load("en_core_web_lg")
    logger.info("spaCy models loaded")
except OSError as exc:
    raise RuntimeError(
        "spaCy models not found — run: python -m spacy download pl_core_news_lg en_core_web_lg"
    ) from exc

_PESEL_RE = re.compile(r"\b\d{11}\b")
_DATE_RE = re.compile(r"\b\d{4}[-./]\d{2}[-./]\d{2}\b|\b\d{2}[-./]\d{2}[-./]\d{4}\b")
_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_ADDRESS_EN_RE = re.compile(
    r"\b\d+\s+[A-Za-z0-9\s]+(Street|St|Road|Rd|Avenue|Ave|Drive|Dr|Lane|Ln)\b",
    re.IGNORECASE,
)
_DATE_PL_RE = re.compile(
    r"\b\d{1,2}\s+(?:"
    r"stycz\w*|lut\w*|mar\w*|kwiet\w*|"
    r"maj(?:a|em|u|o)?|czerw\w*|lip\w*|sierp\w*|"
    r"wrze\w*|paź\w*|"  # pa + ź (U+017A) for październik
    r"listopad\w*|grud\w*"
    r")\.?\s+\d{2,4}(?!\d)",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?48[\s\-]?)?([4-9]\d{2})[\s\-]?(\d{3})[\s\-]?(\d{3})(?!\d)"
)

# Polish model (pl_core_news_lg) uses lowercase labels; English uses uppercase
_NLP_LABELS: dict[str, str] = {
    "persName": "[UTAJNIONO: IMIE/NAZWISKO]",
    "placeName": "[UTAJNIONO: ADRES]",
    "geogName": "[UTAJNIONO: ADRES]",
    "PERSON": "[UTAJNIONO: IMIE/NAZWISKO]",
    "GPE": "[UTAJNIONO: ADRES]",
    "LOC": "[UTAJNIONO: ADRES]",
    "DATE": "[UTAJNIONO: DATA]",
    "date": "[UTAJNIONO: DATA]",
    "time": "[UTAJNIONO: DATA]",
}


def _already_added(word: WordDict, items: list[WordDict]) -> bool:
    return any(e["x"] == word["x"] and e["y"] == word["y"] for e in items)


def find_personal_data(words: list[WordDict]) -> list[WordDict]:
    """Return copies of word dicts that contain sensitive data, extended with 'rodzaj_danych'."""
    full_text = " ".join(s["tekst"] for s in words)
    found: list[WordDict] = []

    for word in words:
        text = word["tekst"]
        item = word.copy()
        if _PESEL_RE.match(text):
            item["rodzaj_danych"] = "[UTAJNIONO: PESEL]"
            found.append(item)
        elif _ADDRESS_EN_RE.match(text):
            item["rodzaj_danych"] = "[UTAJNIONO: ADRES]"
            found.append(item)
        elif _EMAIL_RE.match(text):
            item["rodzaj_danych"] = "[UTAJNIONO: EMAIL]"
            found.append(item)
        elif _PHONE_RE.match(text):
            item["rodzaj_danych"] = "[UTAJNIONO: TELEFON]"
            found.append(item)

    for pattern in [_DATE_RE, _DATE_PL_RE]:
        for match in pattern.finditer(full_text):
            for fragment in match.group().split():
                for word in words:
                    if word["tekst"].lower() == fragment.lower() and not _already_added(
                        word, found
                    ):
                        item = word.copy()
                        item["rodzaj_danych"] = "[UTAJNIONO: DATA]"
                        found.append(item)

    if len(full_text.strip()) < _MIN_TEXT_LEN_FOR_NLP:
        return found

    try:
        lang = detect(full_text)
    except LangDetectException:
        lang = "pl"

    if lang == "en":
        doc = _nlp_en(full_text)
        active = ["PERSON", "DATE"]
    else:
        doc = _nlp_pl(full_text)
        active = ["persName", "placeName", "geogName", "date", "time"]

    for ent in doc.ents:
        if ent.label_ in active:
            label = _NLP_LABELS.get(ent.label_, "[UTAJNIONO]")
            for fragment in ent.text.split():
                for word in words:
                    if word["tekst"].lower() == fragment.lower() and not _already_added(
                        word, found
                    ):
                        item = word.copy()
                        item["rodzaj_danych"] = label
                        found.append(item)

    return found
