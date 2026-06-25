from __future__ import annotations

import re
import unicodedata

_PUNCT_PATTERN = re.compile(r"[^\w\s]")


def _strip_diacritics(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return nfkd.encode("ascii", "ignore").decode("ascii")


def normalize_text(text: str) -> str:
    ascii_text = _strip_diacritics(text.lower())
    cleaned = _PUNCT_PATTERN.sub(" ", ascii_text)
    return " ".join(cleaned.split())
