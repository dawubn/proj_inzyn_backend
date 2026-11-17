"""Named entities for redaction: HerBERT legal NER for Polish, spaCy for English."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from app.core.config import settings
from app.services.redaction_rules import (
    ACCOUNT,
    ADDRESS,
    DOCUMENT_NUMBER,
    EMAIL,
    HEALTH,
    PERSON,
    PHONE,
    Span,
    is_title_word,
)

logger = structlog.get_logger(__name__)

_HERBERT_DIR = "herbert-polish-legal-ner-ocr"
_MAX_TOKENS = 510
_CHUNK_TOKENS = 400
_OVERLAP_TOKENS = 60
_HERBERT_LABELS = {
    "PER": PERSON,
    "LOC": ADDRESS,
    "EMAIL": EMAIL,
    "PHONE": PHONE,
    "IBAN": ACCOUNT,
    "ID": DOCUMENT_NUMBER,
    "MEDICAL_ID": HEALTH,
    "DIAGNOSIS": HEALTH,
}
# DATE entities are not mapped: dates stay visible for the analysis.
_SPACY_LABELS = {"persName": PERSON, "PERSON": PERSON}


class _Herbert:
    def __init__(self, model_dir: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        config = json.loads((model_dir / "config.json").read_text(encoding="utf-8"))
        self._id2label = {int(k): v for k, v in config["id2label"].items()}
        self._per = (config["label2id"]["B-PER"], config["label2id"]["I-PER"])
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = settings.REDACTION_NER_THREADS
        self._session = ort.InferenceSession(
            str(model_dir / "onnx" / "model_quantized.onnx"),
            options,
            providers=["CPUExecutionProvider"],
        )
        self._inputs = {i.name for i in self._session.get_inputs()}

    def entities(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for start, end in self._chunks(text):
            spans.extend(
                (start + a, start + b, label) for a, b, label in self._predict(text[start:end])
            )
        return spans

    def _chunks(self, text: str) -> list[tuple[int, int]]:
        """Whole lines grouped so that each chunk fits the model window."""
        chunks: list[tuple[int, int]] = []
        start, tokens, offset = 0, 0, 0
        for line in text.split("\n"):
            encoding = self._tokenizer.encode(line, add_special_tokens=False)
            count = len(encoding.ids) + 1
            if tokens and tokens + count > _CHUNK_TOKENS:
                chunks.append((start, offset - 1))
                start, tokens = offset, 0
            if count > _CHUNK_TOKENS:
                # A line longer than the window is read in overlapping parts.
                chunks.extend(
                    (offset + a, offset + b) for a, b in _windows(encoding.offsets, len(line))
                )
                start, tokens = offset + len(line) + 1, 0
            else:
                tokens += count
            offset += len(line) + 1
        if start < len(text):
            chunks.append((start, len(text)))
        return chunks

    def _predict(self, text: str) -> list[Span]:
        encoding = self._tokenizer.encode(text)
        ids = np.array([encoding.ids[:_MAX_TOKENS]], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
        if "token_type_ids" in self._inputs:
            feeds["token_type_ids"] = np.zeros_like(ids)
        logits = self._session.run(None, feeds)[0][0]
        exp = np.exp(logits - logits.max(-1, keepdims=True))
        probs = exp / exp.sum(-1, keepdims=True)

        spans: list[Span] = []
        current: list[Any] | None = None
        for index, (start, end) in enumerate(encoding.offsets[:_MAX_TOKENS]):
            if start == end:
                if current:
                    spans.append((current[0], current[1], current[2]))
                current = None
                continue
            label = self._id2label[int(probs[index].argmax())]
            # Recall first for people, as recommended by the model authors.
            if label == "O" and probs[index, self._per[0]] + probs[index, self._per[1]] >= 0.2:
                label = "I-PER" if current and current[2] == "PER" else "B-PER"
            if label == "O":
                if current:
                    spans.append((current[0], current[1], current[2]))
                current = None
                continue
            tag, kind = label.split("-", 1)
            if tag == "B" or current is None or current[2] != kind:
                if current:
                    spans.append((current[0], current[1], current[2]))
                current = [start, end, kind]
            else:
                current[1] = end
        if current:
            spans.append((current[0], current[1], current[2]))
        return [(a, b, _HERBERT_LABELS[k]) for a, b, k in spans if k in _HERBERT_LABELS]


def _windows(offsets: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    """Character ranges of overlapping token windows over one long line."""
    windows = []
    step = _CHUNK_TOKENS - _OVERLAP_TOKENS
    for first in range(0, len(offsets), step):
        last = min(len(offsets), first + _CHUNK_TOKENS) - 1
        end = offsets[last][1] if last + 1 < len(offsets) else length
        windows.append((offsets[first][0], end))
        if last + 1 >= len(offsets):
            break
    return windows


_herbert: _Herbert | None = None
_herbert_loaded = False
_spacy: dict[str, Any] = {}
_spacy_loaded: set[str] = set()


def _load_herbert() -> _Herbert | None:
    global _herbert, _herbert_loaded  # noqa: PLW0603
    if _herbert_loaded:
        return _herbert
    model_dir = Path(settings.REDACTION_MODELS_DIR) / _HERBERT_DIR
    if not (model_dir / "onnx" / "model_quantized.onnx").exists():
        _herbert_loaded = True
        logger.warning("HerBERT NER model not found, using spaCy", path=str(model_dir))
        return None
    try:
        _herbert = _Herbert(model_dir)
    except Exception:
        # Not marked as loaded, so the next document tries again.
        logger.exception("HerBERT NER model failed to load, using spaCy")
        return None
    _herbert_loaded = True
    logger.info("HerBERT NER model loaded", path=str(model_dir))
    return _herbert


def _load_spacy(language: str) -> Any:
    if language in _spacy_loaded:
        return _spacy.get(language)
    _spacy_loaded.add(language)
    import spacy

    name = "pl_core_news_lg" if language == "pl" else "en_core_web_lg"
    try:
        _spacy[language] = spacy.load(name, exclude=["parser", "lemmatizer"])
    except OSError:
        logger.warning("spaCy model not found, NER disabled for language", model=name)
        return None
    return _spacy[language]


def models_available() -> bool:
    return _load_herbert() is not None or _load_spacy("pl") is not None


def herbert_available() -> bool:
    return _load_herbert() is not None


def _spacy_entities(text: str, language: str) -> list[Span]:
    nlp = _load_spacy(language)
    if nlp is None:
        return []
    # Capitalised text is read much better than lines written in capitals.
    prepared = "\n".join(
        line.title() if line.isupper() and len(line.title()) == len(line) else line
        for line in text.split("\n")
    )
    return [
        (ent.start_char, ent.end_char, _SPACY_LABELS[ent.label_])
        for ent in nlp(prepared).ents
        if ent.label_ in _SPACY_LABELS
    ]


def _plausible(text: str, label: str) -> bool:
    value = text.strip()
    if label == PERSON:
        return bool(re.search(r"[^\W\d_]{2,}", value))
    if label in (DOCUMENT_NUMBER, ACCOUNT, PHONE):
        return sum(c.isdigit() for c in value) >= 4
    return bool(value)


_REFERENCE_CONTEXT = re.compile(
    r"(?:faktur\w*|sygn\w*|sprawy|sprawa|sprawie|umowy|umowa|zamowien\w*|zlecen\w*|wniosku|polis\w*"
    r"|decyzj\w*|pisma|invoice|claim|case|ref\w*|order|contract|agreement)\W*"
    r"(?:nr|no|numer|number|akt)?\W*(?:[IVXL]+\s+)?(?:[A-Z][A-Za-z]{0,3}\s*)?$",
    re.IGNORECASE,
)
_REFERENCE_SHAPE = re.compile(
    r"^[A-Z]{0,6}\s?(?:/[\w.]+){1,4}$|^[\w.]+(?:/[\w.]+){2,}$"
    r"|^(?:[IVXL]+\s)?[A-Za-z]{1,4}\s\d+/\d{2,4}$"
)


_EMAIL_PART = re.compile(r"[@.]\s?[\w\-]{2,}")
_REFERENCE_LABELS = (DOCUMENT_NUMBER, ACCOUNT, HEALTH, ADDRESS, PHONE)


def _is_reference(text: str, start: int, end: int) -> bool:
    """Invoice, contract and case numbers are needed for the analysis."""
    value = text[start:end].strip()
    before = text[max(0, start - 30) : start].translate(str.maketrans("ąćęłńóśźż", "acelnoszz"))
    return bool(_REFERENCE_CONTEXT.search(before) or _REFERENCE_SHAPE.match(value))


def _without_titles(text: str, start: int, end: int) -> tuple[int, int]:
    """ "Pan", "Adwokat" or "Mecenas" next to a name is kept, only the name is masked."""
    words = list(re.finditer(r"\S+", text[start:end]))
    while words and is_title_word(words[0].group(0)):
        words.pop(0)
    if not words:
        return start, start
    return start + words[0].start(), start + words[-1].end()


def _to_words(text: str, start: int, end: int) -> tuple[int, int]:
    """Spans of subword models may start or end inside a word."""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    return start, end


def _per_line(text: str, spans: list[Span]) -> list[Span]:
    """An entity that wraps onto the next line becomes one span per line."""
    result: list[Span] = []
    for start, end, label in spans:
        cursor = start
        for part in text[start:end].split("\n"):
            if part.strip():
                left = cursor + len(part) - len(part.lstrip())
                result.append((left, cursor + len(part.rstrip()), label))
            cursor += len(part) + 1
    return result


def find_entities(text: str, language: str) -> list[Span]:
    """Entity spans mapped to redaction labels, one span per line."""
    herbert = _load_herbert()
    spans = herbert.entities(text) if herbert is not None else []
    if language == "en":
        spans += _spacy_entities(text, "en")
    elif herbert is None:
        spans += _spacy_entities(text, "pl")
    result: list[Span] = []
    for span_start, span_end, label in _per_line(text, spans):
        start, end = span_start, span_end
        if label in (PERSON, DOCUMENT_NUMBER, ACCOUNT, PHONE, EMAIL):
            start, end = _to_words(text, span_start, span_end)
        if label == PERSON:
            start, end = _without_titles(text, start, end)
            if start >= end:
                continue
        if label == EMAIL:
            # Stop after the last part of the domain, not at the words that follow it.
            parts = list(_EMAIL_PART.finditer(text, start, end))
            if not parts:
                continue
            end = parts[-1].end()
        if not _plausible(text[start:end], label):
            continue
        if label in _REFERENCE_LABELS and _is_reference(text, start, end):
            continue
        if label == ADDRESS and re.fullmatch(r"\S*\d\S*", text[start:end].strip()):
            continue  # a single code such as a claim number, not a street
        result.append((start, end, label))
    return result
