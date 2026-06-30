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
    r"\[?\s*utajni\w*\s*[:.]?\s*"
    r"(?P<label>data|adres|imie\s*/?\s*nazwisko|imi[eę]\s*/?\s*nazwisko|im|"
    r"pesel|email|telefon)\s*\]?",
    re.IGNORECASE,
)

_SIGNATURE_RE = re.compile(
    r"\b(?:podpis|podpisano|wlasnoreczny\s+podpis|własnoręczny\s+podpis|"
    r"czytelny\s+podpis|podpis\s+elektroniczny|kwalifikowany\s+podpis|signature|signed)\b",
    re.IGNORECASE,
)
_NEGATIVE_SIGNATURE_RE = re.compile(
    r"\b(?:brak|bez|nie)\s+podpis\w*\b|\bmiejsce\s+pozostawione\s+puste\b",
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
        "nabywca",
        "sprzedawca",
        "strona",
        "stronami",
        "umawiajace sie strony",
        "umawiające się strony",
        "powodka",
        "powódka",
    ),
    "has_subject_section": (
        "przedmiot umowy",
        "przedmiot",
        "zakres umowy",
        "obowiazki stron",
        "obowiązki stron",
        "zakres prac",
        "zakres uslug",
        "zakres usług",
        "opis przedmiotu",
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
        "wydzial cywilny",
        "wydział cywilny",
    ),
    "has_claim_section": (
        "pozew",
        "zadanie pozwu",
        "żądanie pozwu",
        "wnosze o",
        "wnoszę o",
        "domagam sie",
        "domagam się",
        "zasadzenie",
        "zasądzenie",
        "o zaplate",
        "o zapłatę",
        "wartosc przedmiotu sporu",
        "wartość przedmiotu sporu",
    ),
    "has_justification_section": (
        "uzasadnienie",
        "stan faktyczny",
        "dowod",
        "dowód",
        "dowody",
        "okolicznosci",
        "okoliczności",
        "fakty",
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
        "ustanawiam pelnomocnikiem",
        "ustanawiam pełnomocnikiem",
    ),
    "has_authorization_scope_section": (
        "udzielam pelnomocnictwa",
        "udzielam pełnomocnictwa",
        "upowazniam",
        "upoważniam",
        "upelnomocniam",
        "upełnomocniam",
        "do reprezentowania",
        "reprezentowania mnie",
        "w moim imieniu",
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
        "zalacznik nr",
        "załącznik nr",
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

_NEGATED_SECTION_CONTEXT_WORDS = frozenset(
    {
        "brak",
        "braki",
        "brakuje",
        "brakujacy",
        "brakujace",
        "brakujaca",
        "bez",
    }
)


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
    if normalized in {"im", "imie nazwisko"}:
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


def _has_signature_marker(normalized_text: str) -> bool:
    if _NEGATIVE_SIGNATURE_RE.search(normalized_text):
        return False
    return bool(_SIGNATURE_RE.search(normalized_text))


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    tokens = normalize_search_text(keyword).split()
    token_patterns = [rf"{re.escape(token)}\w*" for token in tokens]
    pattern = r"\s+".join(token_patterns)
    return re.compile(rf"\b{pattern}\b")


def _is_negated_keyword_match(search_text: str, start: int) -> bool:
    previous_tokens = search_text[:start].split()[-5:]
    return any(token in _NEGATED_SECTION_CONTEXT_WORDS for token in previous_tokens)


def _contains_section_keyword(search_text: str, keyword: str) -> bool:
    pattern = _keyword_pattern(keyword)
    return any(
        not _is_negated_keyword_match(search_text, match.start())
        for match in pattern.finditer(search_text)
    )


class FormalDocumentExtractor:
    """Extracts MVP-level formal fields from OCR text."""

    def extract(
        self,
        raw_ocr_result: dict[str, Any] | None,
        fallback_document_type: DocumentType = DocumentType.UNKNOWN,
    ) -> FormalExtractionResult:
        text = extract_text_from_ocr_raw(raw_ocr_result)
        normalized = normalize_text(text)
        search_text = normalize_search_text(text)

        document_type, confidence = self._classify(search_text, fallback_document_type)
        date_candidates = _find_date_candidates(text)
        detected_sections = self._detect_sections(search_text)
        redaction_labels = _find_redaction_labels(text)

        fields: dict[str, Any] = {
            "document_text": text,
            "has_text": bool(text.strip()),
            "has_date": bool(date_candidates),
            "date_candidates": date_candidates[:5],
            "has_signature": _has_signature_marker(normalized),
            "redaction_labels": sorted(redaction_labels),
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
        self, search_text: str, fallback_document_type: DocumentType
    ) -> tuple[DocumentType, float]:
        scores = {
            doc_type: sum(
                1 for keyword in keywords if normalize_search_text(keyword) in search_text
            )
            for doc_type, keywords in _CLASSIFICATION_KEYWORDS.items()
        }
        best_type, best_score = max(scores.items(), key=lambda item: item[1])

        if best_score == 0:
            if fallback_document_type != DocumentType.UNKNOWN:
                return fallback_document_type, 0.5
            return DocumentType.UNKNOWN, 0.0

        max_possible = max(len(_CLASSIFICATION_KEYWORDS[best_type]), 1)
        return best_type, round(min(best_score / max_possible, 1.0), 4)

    def _detect_sections(self, search_text: str) -> set[str]:
        detected: set[str] = set()
        for field_name, keywords in _SECTION_KEYWORDS.items():
            if any(_contains_section_keyword(search_text, keyword) for keyword in keywords):
                detected.add(field_name)
        return detected
