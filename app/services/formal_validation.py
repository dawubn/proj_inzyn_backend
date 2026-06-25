from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.enums.document import DocumentType

_PL_TRANSLATION = str.maketrans(
    {
        "ą": "a",
        "ć": "c",
        "ę": "e",
        "ł": "l",
        "ń": "n",
        "ó": "o",
        "ś": "s",
        "ź": "z",
        "ż": "z",
    }
)

_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}"
    r"|"
    r"\d{1,2}\s+"
    r"(?:stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|"
    r"pazdziernika|października|listopada|grudnia)"
    r"\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)

_SIGNATURE_RE = re.compile(
    r"\b(?:podpis|podpisano|wlasnoreczny\s+podpis|własnoręczny\s+podpis|signature)\b",
    re.IGNORECASE,
)

_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "has_parties_section": (
        "strony umowy",
        "strony",
        "powod",
        "powód",
        "pozwany",
        "strona powodowa",
        "strona pozwana",
        "zamawiajacy",
        "zamawiający",
        "wykonawca",
        "zleceniodawca",
        "zleceniobiorca",
        "sprzedajacy",
        "sprzedający",
        "kupujacy",
        "kupujący",
    ),
    "has_subject_section": (
        "przedmiot umowy",
        "przedmiot",
        "zakres umowy",
        "obowiazki stron",
        "obowiązki stron",
    ),
    "has_court_section": (
        "sad rejonowy",
        "sąd rejonowy",
        "sad okregowy",
        "sąd okręgowy",
        "sad apelacyjny",
        "sąd apelacyjny",
        "do sadu",
        "do sądu",
    ),
    "has_claim_section": (
        "pozew",
        "zadanie pozwu",
        "żądanie pozwu",
        "wnosze o",
        "wnoszę o",
        "domagam sie",
        "domagam się",
        "wartosc przedmiotu sporu",
        "wartość przedmiotu sporu",
    ),
    "has_justification_section": (
        "uzasadnienie",
        "stan faktyczny",
        "dowod",
        "dowód",
    ),
    "has_principal_section": (
        "mocodawca",
        "udzielajacy pelnomocnictwa",
        "udzielający pełnomocnictwa",
        "dane mocodawcy",
    ),
    "has_attorney_section": (
        "pelnomocnik",
        "pełnomocnik",
        "dane pelnomocnika",
        "dane pełnomocnika",
    ),
    "has_authorization_scope_section": (
        "udzielam pelnomocnictwa",
        "udzielam pełnomocnictwa",
        "upowazniam",
        "upoważniam",
        "do reprezentowania",
        "zakres pelnomocnictwa",
        "zakres pełnomocnictwa",
    ),
    "has_attachments_section": (
        "zalacznik",
        "załącznik",
        "zalaczniki",
        "załączniki",
        "lista zalacznikow",
        "lista załączników",
    ),
}

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


class FormalDocumentExtractor:
    """Extracts MVP-level formal fields from OCR text."""

    def extract(
        self,
        raw_ocr_result: dict[str, Any] | None,
        fallback_document_type: DocumentType = DocumentType.UNKNOWN,
    ) -> FormalExtractionResult:
        text = extract_text_from_ocr_raw(raw_ocr_result)
        normalized = normalize_text(text)

        document_type, confidence = self._classify(normalized, fallback_document_type)
        date_candidates = [match.group(0) for match in _DATE_RE.finditer(text)]
        detected_sections = self._detect_sections(normalized)

        fields: dict[str, Any] = {
            "document_text": text,
            "has_text": bool(text.strip()),
            "has_date": bool(date_candidates),
            "date_candidates": date_candidates[:5],
            "has_signature": bool(_SIGNATURE_RE.search(normalized)),
            "detected_sections": sorted(detected_sections),
        }
        for section_field in _SECTION_KEYWORDS:
            fields[section_field] = section_field in detected_sections

        return FormalExtractionResult(
            document_type=document_type,
            confidence=confidence,
            fields=fields,
        )

    def _classify(
        self, normalized_text: str, fallback_document_type: DocumentType
    ) -> tuple[DocumentType, float]:
        scores = {
            doc_type: sum(1 for keyword in keywords if normalize_text(keyword) in normalized_text)
            for doc_type, keywords in _CLASSIFICATION_KEYWORDS.items()
        }
        best_type, best_score = max(scores.items(), key=lambda item: item[1])

        if best_score == 0:
            if fallback_document_type != DocumentType.UNKNOWN:
                return fallback_document_type, 0.5
            return DocumentType.UNKNOWN, 0.0

        max_possible = max(len(_CLASSIFICATION_KEYWORDS[best_type]), 1)
        return best_type, round(min(best_score / max_possible, 1.0), 4)

    def _detect_sections(self, normalized_text: str) -> set[str]:
        detected: set[str] = set()
        for field_name, keywords in _SECTION_KEYWORDS.items():
            if any(normalize_text(keyword) in normalized_text for keyword in keywords):
                detected.add(field_name)
        return detected
