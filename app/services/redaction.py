from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, field
from numbers import Real
from pathlib import Path
from typing import ClassVar

import pytesseract
import structlog
from pdf2image import convert_from_path
from PIL import Image, ImageDraw, ImageFont
from pytesseract import Output

from app.core.config import settings
from app.core.exceptions import FileTooLargeError, OCRServiceError, UnsupportedFileTypeError
from app.enums.redaction import SensitiveDataType

logger = structlog.get_logger(__name__)

BoundingBox = tuple[int, int, int, int]
WordOffset = tuple[int, int, BoundingBox]

_MIN_OCR_CONFIDENCE = 30.0
_MIN_OCR_IMAGE_SIDE_PX = 1500
_REDACTION_PADDING_PX = 14
_SAME_LINE_TOLERANCE_PX = 8
_HORIZONTAL_MERGE_GAP_PX = 80
_LABEL_PADDING_PX = 4
_FIELD_EMPTY_VALUE_FALLBACK_WIDTH_PX = 420
_FIELD_VALUE_LIMITS: dict[SensitiveDataType, int] = {
    SensitiveDataType.PERSON: 4,
    SensitiveDataType.ORGANIZATION: 6,
    SensitiveDataType.ADDRESS: 5,
    SensitiveDataType.EMAIL: 4,
    SensitiveDataType.PHONE: 4,
    SensitiveDataType.CONTACT: 4,
    SensitiveDataType.DATE: 4,
    SensitiveDataType.PESEL: 2,
    SensitiveDataType.NIP: 3,
    SensitiveDataType.REGON: 2,
    SensitiveDataType.IBAN: 5,
    SensitiveDataType.ID_NUMBER: 2,
    SensitiveDataType.PASSPORT_NUMBER: 2,
}
_FIELD_EMPTY_VALUE_FALLBACK_TYPES = {
    SensitiveDataType.EMAIL,
    SensitiveDataType.PHONE,
    SensitiveDataType.CONTACT,
    SensitiveDataType.DATE,
    SensitiveDataType.PESEL,
    SensitiveDataType.NIP,
    SensitiveDataType.REGON,
    SensitiveDataType.IBAN,
    SensitiveDataType.ID_NUMBER,
    SensitiveDataType.PASSPORT_NUMBER,
}


@dataclass(frozen=True)
class DetectedSpan:
    start: int
    end: int
    data_type: SensitiveDataType


@dataclass(frozen=True)
class WordBox:
    text: str
    left: int
    top: int
    width: int
    height: int

    @property
    def bbox(self) -> BoundingBox:
        return (self.left, self.top, self.left + self.width, self.top + self.height)


@dataclass(frozen=True)
class RedactionBox:
    box: BoundingBox
    data_type: SensitiveDataType


@dataclass(frozen=True)
class PageRedactionReport:
    page_number: int
    word_count: int
    findings_count: int
    redacted_boxes_count: int
    detected_types: set[SensitiveDataType] = field(default_factory=set)


@dataclass(frozen=True)
class RedactionResult:
    output_path: Path
    output_filename: str
    media_type: str
    temp_dir: Path
    reports: list[PageRedactionReport]

    @property
    def findings_count(self) -> int:
        return sum(report.findings_count for report in self.reports)

    @property
    def redacted_boxes_count(self) -> int:
        return sum(report.redacted_boxes_count for report in self.reports)

    @property
    def pages_count(self) -> int:
        return len(self.reports)

    @property
    def detected_types(self) -> list[SensitiveDataType]:
        return sorted(
            {data_type for report in self.reports for data_type in report.detected_types},
            key=lambda data_type: data_type.value,
        )


# Checksum validators

_PESEL_DIGITS = 11
_NIP_DIGITS = 10
_REGON9_DIGITS = 9
_REGON14_DIGITS = 14
_CHECKSUM_OVERFLOW = 10


def _valid_pesel(raw: str) -> bool:
    digits = raw.strip()
    if len(digits) != _PESEL_DIGITS or not digits.isdigit():
        return False
    weights = (1, 3, 7, 9, 1, 3, 7, 9, 1, 3)
    total = sum(int(digits[i]) * weights[i] for i in range(10))
    check = (_CHECKSUM_OVERFLOW - (total % _CHECKSUM_OVERFLOW)) % _CHECKSUM_OVERFLOW
    return check == int(digits[10])


def _valid_nip(raw: str) -> bool:
    digits = re.sub(r"[\s\-]", "", raw)
    if len(digits) != _NIP_DIGITS or not digits.isdigit():
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    total = sum(int(digits[i]) * weights[i] for i in range(9))
    check = total % 11
    return check == int(digits[9])


def _valid_regon9(digits: str) -> bool:
    if len(digits) != _REGON9_DIGITS or not digits.isdigit():
        return False
    weights = (8, 9, 2, 3, 4, 5, 6, 7)
    total = sum(int(digits[i]) * weights[i] for i in range(8))
    check = total % 11
    if check == _CHECKSUM_OVERFLOW:
        check = 0
    return check == int(digits[8])


def _valid_regon(raw: str) -> bool:
    digits = re.sub(r"[\s\-]", "", raw)
    if len(digits) == _REGON9_DIGITS:
        return _valid_regon9(digits)
    if len(digits) == _REGON14_DIGITS:
        if not _valid_regon9(digits[:9]):
            return False
        weights = (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8)
        total = sum(int(digits[i]) * weights[i] for i in range(13))
        check = total % 11
        if check == _CHECKSUM_OVERFLOW:
            check = 0
        return check == int(digits[13])
    return False


_CHECKSUM_VALIDATORS = {
    SensitiveDataType.PESEL: _valid_pesel,
    SensitiveDataType.NIP: _valid_nip,
    SensitiveDataType.REGON: _valid_regon,
}


# Detector
class SensitiveDataDetector:
    _PERSON_FIRST_NAMES: ClassVar[str] = (
        "Adam|Adrian|Agnieszka|Aleksandra|Aleksander|Alicja|Andrzej|Anna|Antoni|"
        "Barbara|Bartosz|Beata|Cezary|Damian|Daniel|Dariusz|Dominik|Dorota|"
        "Ewa|Filip|Grzegorz|Hanna|Hubert|Jakub|Jan|Joanna|Jolanta|Julia|"
        "Kacper|Kamil|Karolina|Katarzyna|Kinga|Krzysztof|Laura|Magdalena|"
        "Marek|Maria|Mariusz|Marcin|Marta|Mateusz|Michal|Michał|Monika|"
        "Natalia|Olga|Patryk|Pawel|Paweł|Piotr|Rafal|Rafał|Robert|Sebastian|"
        "Tomasz|Wiktoria|Wojciech|Zofia|John|Jane|Michael|Mike|Robert|David|"
        "James|William|Richard|Thomas|Mary|Patricia|Jennifer|Linda|Elizabeth|"
        "Maria|Jose|José|Juan|Carlos|Luis|Ana|Antonio|Jean|Pierre|Marie|"
        "Michel|Thomas|Sophie|Hans|Peter|Klaus|Anna|Giovanni|Giuseppe|Marco"
    )
    _PATTERNS: ClassVar[list[tuple[SensitiveDataType, re.Pattern[str]]]] = [
        (
            SensitiveDataType.IBAN,
            re.compile(r"\b[A-Z]{2}\d{2}[\s-]?(?:\d{4}[\s-]?){4,7}\d{1,4}\b", re.I),
        ),
        # PESEL validated by checksum after match
        (SensitiveDataType.PESEL, re.compile(r"(?<!\d)\d{11}(?!\d)")),
        # NIP validated by checksum after match; flexible separator for OCR
        (SensitiveDataType.NIP, re.compile(r"\b\d{3}[-\s]?\d{3}[-\s]?\d{2}[-\s]?\d{2}\b")),
        # REGON validated by checksum after match
        (SensitiveDataType.REGON, re.compile(r"\b(?:\d{14}|\d{9})\b")),
        (
            SensitiveDataType.EMAIL,
            re.compile(r"\b[A-Za-z0-9._%+\-]+[@Q][A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        ),
        (
            SensitiveDataType.PHONE,
            re.compile(r"(?<!\d)(?:\+\d{1,3}[\s-]?)?(?:\d{3}[\s-]?){2}\d{3}(?!\d)"),
        ),
        # Date: validate day/month ranges and require 4-digit year (19xx or 20xx)
        (
            SensitiveDataType.DATE,
            re.compile(r"\b(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-](?:19|20)\d{2}\b"),
        ),
        # Long-form Polish date: "28 stycznia 2019 r." — OCR may strip diacritics
        (
            SensitiveDataType.DATE,
            re.compile(
                r"\b(?:0?[1-9]|[12]\d|3[01])\s+"
                r"(?:stycz[nń]ia|lut[eę]go|marca|kwie[tć]nia|maja|czerw?ca|"
                r"lip[cć]a|sierp[nń]ia|wrze[sś]nia|pa[zź]dzier[nń]ika|list[oó]pada|grud[nń]ia)"
                r"\s+(?:19|20)\d{2}(?:\s*r\.?)?\b",
                re.I | re.UNICODE,
            ),
        ),
        (SensitiveDataType.POSTAL_CODE, re.compile(r"(?<![\d-])\d{2}-\d{3}(?![\d-])")),
        (SensitiveDataType.ID_NUMBER, re.compile(r"\b[A-Z]{3}\s?\d{6}\b", re.I)),
        (SensitiveDataType.PASSPORT_NUMBER, re.compile(r"\b[A-Z]{2}\s?\d{7}\b", re.I)),
        (
            SensitiveDataType.ADDRESS,
            re.compile(
                r"\b(?:ul\.?|ulica|al\.?|aleja|pl\.?|plac|os\.?|osiedle)\s+"
                r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]{2,}"
                r"(?:[^\S\n]+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]{2,}){0,3}"
                r"\s+\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?",
                re.I,
            ),
        ),
        (
            SensitiveDataType.ADDRESS,
            re.compile(
                r"\b(?:street|st\.?|road|rd\.?|avenue|ave\.?|rue|calle|via|strasse|straße)\s+"
                r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.'-]{2,}"
                r"(?:[^\S\n]+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.'-]{2,}){0,4}"
                r"\s+\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?",
                re.I,
            ),
        ),
        (
            SensitiveDataType.ORGANIZATION,
            re.compile(
                r"\b(?:[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ&.'-]{1,}"
                r"(?:[^\S\n]+|$)){1,5}"
                r"(?i:sp\.?\s*z\.?\s*o\.?o\.?|s\.?a\.?|ltd\.?|llc|gmbh|inc\.?|"
                r"corp\.?|corporation|limited|sarl|sas|bv|nv|srl)\b"
            ),
        ),
        (
            SensitiveDataType.PERSON,
            re.compile(
                rf"\b(?:{_PERSON_FIRST_NAMES})[^\S\n]+"
                r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ'-]{2,}"
                r"(?:[^\S\n]+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ'-]{2,})?\b"
            ),
        ),
    ]
    _CONTEXT_PATTERNS: ClassVar[list[tuple[SensitiveDataType, re.Pattern[str]]]] = [
        (
            SensitiveDataType.PERSON,
            re.compile(
                r"\b(?:imi[eę](?:\s+i\s+nazwisko)?|nazwisko|osoba|klient|pacjent|"
                r"pracownik|reprezentant|name)\s*[:\-]?\s+"
                r"([A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]{2,}"
                r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]{2,}){1,3})",
                re.I,
            ),
        ),
        # Catch surnames (and names) appearing after signing/issuing labels even
        # when the first name is not in the known-first-names list.
        (
            SensitiveDataType.PERSON,
            re.compile(
                r"\b(?:podpis(?:ano|a[łl]|a[lł]a)?|wystawił|wystawi[lł]a|"
                r"sporz[aą]dzi[lł]|sporz[aą]dzi[lł]a|signed\s+by|prepared\s+by|"
                r"issued\s+by|authorized\s+by|upowa[zż]ni[oó]ny)\s*[:\-]?\s+"
                r"([A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ'-]{1,}"
                r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ'-]{1,})?)",
                re.I | re.UNICODE,
            ),
        ),
        (
            SensitiveDataType.ADDRESS,
            re.compile(
                r"\b(?:adres|address|zamieszka[lł]y|siedziba)\s*[:\-]?\s+"
                r"((?:ul\.?|ulica|street|st\.?|al\.?|aleja|avenue|ave\.?|pl\.?|plac)\s+"
                r"[A-ZĄĆĘŁŃÓŚŹŻ][\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ.-]{1,}(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ]\w*)?\s+\d+[A-Z]?(?:/\d+[A-Z]?)?)",
                re.I,
            ),
        ),
        (
            SensitiveDataType.ADDRESS,
            # Requires an explicit separator to avoid matching "city" in prose text.
            # Captures at most two words to handle compound names like "New York".
            re.compile(
                r"\b(?:city|miasto)\s*[:\-]\s+"
                r"([A-ZĄĆĘŁŃÓŚŹŻ][A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ-]{1,30}"
                r"(?:\s+[A-ZĄĆĘŁŃÓŚŹŻ][A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ-]{1,30})?)",
                re.I,
            ),
        ),
        (
            SensitiveDataType.CONTACT,
            re.compile(
                r"\b(?:kontakt|contact|e-mail|email|mail|telefon|phone|tel\.?|mobile|"
                r"kom\.?|cell)(?:owy|owa)?[^\S\n]*[:\-][^\S\n]+"
                r"(\S+(?:[^\S\n]+\S+){0,4})",
                re.I,
            ),
        ),
    ]

    def detect(self, text: str) -> list[DetectedSpan]:
        spans: list[DetectedSpan] = []
        for data_type, pattern in self._PATTERNS:
            validator = _CHECKSUM_VALIDATORS.get(data_type)
            for match in pattern.finditer(text):
                if validator is not None and not validator(match.group()):
                    continue
                candidate = DetectedSpan(match.start(), match.end(), data_type)
                if not self._overlaps_any(candidate, spans):
                    spans.append(candidate)
        for data_type, pattern in self._CONTEXT_PATTERNS:
            for match in pattern.finditer(text):
                start, end = self._context_value_range(match)
                candidate = DetectedSpan(start, end, data_type)
                if not self._is_fully_covered(candidate, spans):
                    spans.append(candidate)
        return self._merge_adjacent(spans)

    def _overlaps_any(self, candidate: DetectedSpan, spans: list[DetectedSpan]) -> bool:
        return any(candidate.start < span.end and candidate.end > span.start for span in spans)

    def _is_fully_covered(self, candidate: DetectedSpan, spans: list[DetectedSpan]) -> bool:
        return any(span.start <= candidate.start and span.end >= candidate.end for span in spans)

    def _merge_adjacent(self, spans: list[DetectedSpan]) -> list[DetectedSpan]:
        if not spans:
            return []

        merged = [sorted(spans, key=lambda span: span.start)[0]]
        for span in sorted(spans, key=lambda item: item.start)[1:]:
            last = merged[-1]
            if span.start <= last.end and span.data_type == last.data_type:
                merged[-1] = DetectedSpan(last.start, max(last.end, span.end), last.data_type)
            else:
                merged.append(span)
        return merged

    def _context_value_range(self, match: re.Match[str]) -> tuple[int, int]:
        if match.lastindex:
            return match.start(1), match.end(1)
        return match.start(), match.end()


class RedactionService:
    _PERSON_LABEL_KEYS: ClassVar[set[str]] = {
        "imie",
        "imiona",
        "nazwisko",
        "osoba",
        "klient",
        "pacjent",
        "pracownik",
        "reprezentant",
        "name",
        "firstname",
        "first-name",
        "lastname",
        "last-name",
        "surname",
        "fullname",
        "full-name",
        "person",
        "customer",
        "client",
        "patient",
        "employee",
        "representative",
        "nom",
        "prenom",
        "prénom",
        "nombre",
        "apellido",
        "apellidos",
        "vorname",
        "nachname",
        "familienname",
        "nome",
        "cognome",
    }
    _ADDRESS_LABEL_KEYS: ClassVar[set[str]] = {
        "adres",
        "address",
        "zamieszkaly",
        "zamieszkały",
        "siedziba",
        "ul",
        "ulica",
        "aleja",
        "plac",
        "osiedle",
        "street",
        "st",
        "road",
        "rd",
        "avenue",
        "ave",
        "city",
        "postal",
        "postcode",
        "zip",
        "zipcode",
        "zip-code",
        "adresse",
        "rue",
        "ville",
        "direccion",
        "dirección",
        "calle",
        "ciudad",
        "direccionpostal",
        "adressepostale",
        "anschrift",
        "strasse",
        "straße",
        "ort",
        "indirizzo",
        "via",
        "citta",
        "città",
    }
    _CONTACT_LABEL_KEYS: ClassVar[set[str]] = {
        "kontakt",
        "contact",
        "telefon",
        "phone",
        "telephone",
        "tel",
        "mobile",
        "cell",
        "cellphone",
        "kom",
        "kontakttelefon",
        "telefono",
        "teléfono",
        "movil",
        "móvil",
        "telephoneportable",
        "telefonnummer",
        "handy",
    }
    _EMAIL_LABEL_KEYS: ClassVar[set[str]] = {
        "email",
        "e-mail",
        "mail",
        "correo",
        "courriel",
        "mel",
        "emailadresse",
    }
    _DATE_LABEL_KEYS: ClassVar[set[str]] = {
        "data",
        "date",
        "datum",
        "fecha",
        "dob",
        "birthdate",
        "birth-date",
        "data-urodzenia",
        "data-wystawienia",
        "data-zawarcia",
        "data-podpisania",
    }
    _ORGANIZATION_LABEL_KEYS: ClassVar[set[str]] = {
        "firma",
        "spolka",
        "spółka",
        "organizacja",
        "pracodawca",
        "kontrahent",
        "company",
        "organization",
        "organisation",
        "employer",
        "contractor",
        "vendor",
        "business",
        "societe",
        "société",
        "entreprise",
        "empresa",
        "compania",
        "compañia",
        "compañía",
        "unternehmen",
        "gesellschaft",
        "azienda",
        "societa",
        "società",
    }
    _ID_LABEL_KEYS: ClassVar[dict[str, SensitiveDataType]] = {
        "pesel": SensitiveDataType.PESEL,
        "nip": SensitiveDataType.NIP,
        "regon": SensitiveDataType.REGON,
        "iban": SensitiveDataType.IBAN,
        "dowod": SensitiveDataType.ID_NUMBER,
        "dowód": SensitiveDataType.ID_NUMBER,
        "identity": SensitiveDataType.ID_NUMBER,
        "id": SensitiveDataType.ID_NUMBER,
        "passport": SensitiveDataType.PASSPORT_NUMBER,
        "paszport": SensitiveDataType.PASSPORT_NUMBER,
        "vat": SensitiveDataType.NIP,
        "tax": SensitiveDataType.NIP,
        "taxid": SensitiveDataType.NIP,
        "tax-id": SensitiveDataType.NIP,
    }
    _FIELD_LABELS: ClassVar[dict[str, SensitiveDataType]] = {}

    def __init__(self, detector: SensitiveDataDetector | None = None) -> None:
        self._detector = detector or SensitiveDataDetector()

    def redact_file(self, filename: str, content: bytes, content_type: str) -> RedactionResult:
        self._validate_file(filename, len(content))

        temp_dir = Path(tempfile.mkdtemp(prefix="cerberdoc-redaction-"))
        input_path = temp_dir / Path(filename).name
        input_path.write_bytes(content)

        try:
            pages = self._load_pages(input_path)
            redacted_pages = []
            reports: list[PageRedactionReport] = []

            for page_number, page in enumerate(pages, start=1):
                redacted_page, report = self._redact_page(page, page_number)
                redacted_pages.append(redacted_page)
                reports.append(report)

            output_path = self._save_pages(redacted_pages, input_path, temp_dir)
            logger.info(
                "Document redacted",
                filename=filename,
                findings_count=sum(report.findings_count for report in reports),
                boxes_count=sum(report.redacted_boxes_count for report in reports),
            )
            return RedactionResult(
                output_path=output_path,
                output_filename=self._output_filename(filename, output_path.suffix),
                media_type=self._media_type(output_path.suffix, content_type),
                temp_dir=temp_dir,
                reports=reports,
            )
        except Exception:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def build_text_with_offsets(self, words: list[WordBox]) -> tuple[str, list[WordOffset]]:
        full_text = ""
        offsets: list[WordOffset] = []
        previous_word: WordBox | None = None
        for word in words:
            if previous_word is not None:
                separator = "\n" if not self._words_on_same_line(previous_word, word) else " "
                full_text += separator
            start = len(full_text)
            full_text += word.text
            end = len(full_text)
            offsets.append((start, end, word.bbox))
            previous_word = word
        return full_text, offsets

    def spans_to_boxes(
        self, spans: list[DetectedSpan], offsets: list[WordOffset]
    ) -> list[RedactionBox]:
        """Map text spans back to image bounding boxes.

        Words belonging to the same span and the same visual line are merged
        into a single wide rectangle so the redaction appears as one solid bar
        rather than a series of disconnected per-word patches.
        """
        boxes: list[RedactionBox] = []
        for span in spans:
            span_bboxes = [
                bbox for start, end, bbox in offsets if start < span.end and end > span.start
            ]
            if not span_bboxes:
                continue
            # Group bboxes by visual line using center-y tolerance — the same
            # algorithm as _group_words_by_line so words that OCR places at
            # slightly different top-y values still end up in the same group.
            lines: list[list[BoundingBox]] = []
            for bbox in span_bboxes:
                y1, y2 = bbox[1], bbox[3]
                center_y = (y1 + y2) / 2
                height = max(y2 - y1, 1)
                placed = False
                for line in lines:
                    line_center = sum((b[1] + b[3]) / 2 for b in line) / len(line)
                    max_h = max(b[3] - b[1] for b in line)
                    tol = max(_SAME_LINE_TOLERANCE_PX, height * 0.6, max_h * 0.6)
                    if abs(center_y - line_center) <= tol:
                        line.append(bbox)
                        placed = True
                        break
                if not placed:
                    lines.append([bbox])
            # Produce one merged rectangle per visual line
            for line_bboxes in lines:
                merged: BoundingBox = (
                    min(b[0] for b in line_bboxes),
                    min(b[1] for b in line_bboxes),
                    max(b[2] for b in line_bboxes),
                    max(b[3] for b in line_bboxes),
                )
                boxes.append(RedactionBox(merged, span.data_type))
        return boxes

    def cleanup(self, temp_dir: Path) -> None:
        shutil.rmtree(temp_dir, ignore_errors=True)

    def _validate_file(self, filename: str, size: int) -> None:
        if size > settings.max_upload_size_bytes:
            raise FileTooLargeError(f"File exceeds limit of {settings.MAX_UPLOAD_SIZE_MB} MB")

        ext = Path(filename).suffix.lstrip(".").lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise UnsupportedFileTypeError(f"Extension .{ext} is not supported")

    def _load_pages(self, input_path: Path) -> list[Image.Image]:
        ext = input_path.suffix.lower()
        try:
            if ext == ".pdf":
                return list(convert_from_path(str(input_path), dpi=300))

            return [Image.open(input_path).convert("RGB")]
        except OCRServiceError:
            raise
        except Exception as exc:
            raise OCRServiceError(f"Could not load document for redaction: {exc}") from exc

    def _redact_page(
        self, image: Image.Image, page_number: int
    ) -> tuple[Image.Image, PageRedactionReport]:
        original_width, original_height = image.size
        words, ocr_size = self._image_to_wordboxes(image)

        if ocr_size != (original_width, original_height):
            scale_x = original_width / ocr_size[0]
            scale_y = original_height / ocr_size[1]
            words = [
                WordBox(
                    text=word.text,
                    left=int(word.left * scale_x),
                    top=int(word.top * scale_y),
                    width=int(word.width * scale_x),
                    height=int(word.height * scale_y),
                )
                for word in words
            ]

        full_text, offsets = self.build_text_with_offsets(words)
        spans = self._detector.detect(full_text)
        boxes = self.spans_to_boxes(spans, offsets)
        field_boxes = self._field_line_boxes(words, image.size)
        boxes.extend(field_boxes)
        redacted = self._draw_redactions(image, boxes)

        return redacted, PageRedactionReport(
            page_number=page_number,
            word_count=len(words),
            findings_count=len(spans) + len(field_boxes),
            redacted_boxes_count=len(boxes),
            detected_types={span.data_type for span in spans} | {box.data_type for box in boxes},
        )

    def _image_to_wordboxes(self, image: Image.Image) -> tuple[list[WordBox], tuple[int, int]]:
        ocr_image = image
        width, height = image.size
        if max(width, height) < _MIN_OCR_IMAGE_SIDE_PX:
            scale = _MIN_OCR_IMAGE_SIDE_PX / max(width, height)
            ocr_image = image.resize(
                (int(width * scale), int(height * scale)),
                Image.Resampling.LANCZOS,
            )

        try:
            data = pytesseract.image_to_data(
                ocr_image,
                lang="pol+eng",
                output_type=Output.DICT,
            )
        except Exception as exc:
            raise OCRServiceError(f"Tesseract OCR failed: {exc}") from exc

        words: list[WordBox] = []
        for index, text in enumerate(data["text"]):
            stripped = str(text).strip()
            if not stripped:
                continue

            confidence = self._parse_confidence(data["conf"][index])
            if confidence <= _MIN_OCR_CONFIDENCE:
                continue

            words.append(
                WordBox(
                    text=stripped,
                    left=int(data["left"][index]),
                    top=int(data["top"][index]),
                    width=int(data["width"][index]),
                    height=int(data["height"][index]),
                )
            )

        return words, ocr_image.size

    def _parse_confidence(self, value: object) -> float:
        if not isinstance(value, str | Real):
            return -1.0
        try:
            return float(value)
        except (TypeError, ValueError):
            return -1.0

    def _field_line_boxes(
        self, words: list[WordBox], image_size: tuple[int, int]
    ) -> list[RedactionBox]:
        result: list[RedactionBox] = []
        for line in self._group_words_by_line(words):
            line_words = sorted(line, key=lambda word: word.left)
            for index, word in enumerate(line_words):
                # Only treat a word as a field label when it is the first token
                # on the line OR carries an explicit separator (colon / dash).
                # This prevents common words like "city" from being misread as
                # labels when they appear mid-sentence in prose text.
                if index > 0 and not word.text.rstrip().endswith((":", "-")):
                    continue

                data_type = self._field_label_type(word.text)
                if data_type is None:
                    continue

                redaction = self._field_value_box(line_words, index, data_type, image_size)
                if redaction is not None:
                    result.append(redaction)
                break
        return result

    def _field_value_box(
        self,
        line_words: list[WordBox],
        label_index: int,
        data_type: SensitiveDataType,
        image_size: tuple[int, int],
    ) -> RedactionBox | None:
        redacted_words = self._field_value_words(line_words, label_index, data_type)
        if redacted_words:
            return RedactionBox(
                (
                    min(w.left for w in redacted_words),
                    min(w.top for w in redacted_words),
                    max(w.left + w.width for w in redacted_words),
                    max(w.top + w.height for w in redacted_words),
                ),
                data_type,
            )

        if data_type not in _FIELD_EMPTY_VALUE_FALLBACK_TYPES:
            return None

        image_width = image_size[0]
        label_word = line_words[label_index]
        x1 = label_word.left + label_word.width + _REDACTION_PADDING_PX
        if x1 >= image_width:
            return None

        return RedactionBox(
            (
                x1,
                label_word.top,
                min(image_width, x1 + _FIELD_EMPTY_VALUE_FALLBACK_WIDTH_PX),
                label_word.top + label_word.height,
            ),
            data_type,
        )

    def _field_value_words(
        self, line_words: list[WordBox], label_index: int, data_type: SensitiveDataType
    ) -> list[WordBox]:
        value_words: list[WordBox] = []
        limit = _FIELD_VALUE_LIMITS.get(data_type, 4)
        for word in line_words[label_index + 1 :]:
            word_label_type = self._field_label_type(word.text)
            if word_label_type is not None and word_label_type != data_type:
                break
            value_words.append(word)
            if len(value_words) >= limit:
                break
        return value_words

    def _group_words_by_line(self, words: list[WordBox]) -> list[list[WordBox]]:
        lines: list[list[WordBox]] = []
        for word in sorted(words, key=lambda item: (item.top, item.left)):
            placed = False
            center_y = word.top + word.height / 2
            for line in lines:
                line_center = sum(item.top + item.height / 2 for item in line) / len(line)
                tolerance = max(_SAME_LINE_TOLERANCE_PX, word.height * 0.7)
                if abs(center_y - line_center) <= tolerance:
                    line.append(word)
                    placed = True
                    break
            if not placed:
                lines.append([word])
        return lines

    def _words_on_same_line(self, first: WordBox, second: WordBox) -> bool:
        first_center = first.top + first.height / 2
        second_center = second.top + second.height / 2
        tolerance = max(_SAME_LINE_TOLERANCE_PX, first.height * 0.7, second.height * 0.7)
        return abs(first_center - second_center) <= tolerance

    def _field_label_type(self, text: str) -> SensitiveDataType | None:
        normalized = self._field_label_key(text)
        if normalized in self._FIELD_LABELS:
            return self._FIELD_LABELS[normalized]

        compact = normalized.replace("-", "")
        return self._FIELD_LABELS.get(compact)

    def _field_label_key(self, text: str) -> str:
        normalized = self._normalize_word(text)
        return re.sub(r"(^[^a-z0-9]+|[^a-z0-9-]+$)", "", normalized)

    def _normalize_word(self, value: str) -> str:
        return (
            value.casefold()
            .replace("ą", "a")
            .replace("ć", "c")
            .replace("ę", "e")
            .replace("ł", "l")
            .replace("ń", "n")
            .replace("ó", "o")
            .replace("ś", "s")
            .replace("ź", "z")
            .replace("ż", "z")
        )

    def _draw_redactions(self, image: Image.Image, boxes: list[RedactionBox]) -> Image.Image:
        result = image.copy().convert("RGB")
        if not boxes:
            return result

        width, height = result.size
        expanded = [
            RedactionBox(self._expand_box(redaction.box, width, height), redaction.data_type)
            for redaction in boxes
        ]
        merged = self._merge_boxes(expanded)

        draw = ImageDraw.Draw(result)
        for redaction in merged:
            draw.rectangle(redaction.box, fill=(0, 0, 0))
            self._draw_label(draw, redaction)
        return result

    def _expand_box(self, box: BoundingBox, image_width: int, image_height: int) -> BoundingBox:
        x1, y1, x2, y2 = box
        return (
            max(0, x1 - _REDACTION_PADDING_PX),
            max(0, y1 - _REDACTION_PADDING_PX),
            min(image_width, x2 + _REDACTION_PADDING_PX),
            min(image_height, y2 + _REDACTION_PADDING_PX),
        )

    def _merge_boxes(self, boxes: list[RedactionBox]) -> list[RedactionBox]:
        if not boxes:
            return []

        merged = [sorted(boxes, key=lambda box: (box.box[1], box.box[0]))[0]]
        for redaction in sorted(boxes, key=lambda item: (item.box[1], item.box[0]))[1:]:
            last = merged[-1]
            box = redaction.box
            last_box = last.box
            same_line = (
                abs(box[1] - last_box[1]) < _SAME_LINE_TOLERANCE_PX
                and abs(box[3] - last_box[3]) < _SAME_LINE_TOLERANCE_PX
            )
            close_horizontal = box[0] - last_box[2] < _HORIZONTAL_MERGE_GAP_PX
            same_type = redaction.data_type == last.data_type
            if same_line and close_horizontal and same_type:
                merged[-1] = RedactionBox(
                    (
                        last_box[0],
                        min(last_box[1], box[1]),
                        max(last_box[2], box[2]),
                        max(last_box[3], box[3]),
                    ),
                    last.data_type,
                )
            else:
                merged.append(redaction)
        return merged

    def _draw_label(self, draw: ImageDraw.ImageDraw, redaction: RedactionBox) -> None:
        x1, y1, x2, y2 = redaction.box
        label = redaction.data_type.value
        font = ImageFont.load_default()
        label_box = draw.textbbox((0, 0), label, font=font)
        label_width = int(label_box[2] - label_box[0])
        label_height = int(label_box[3] - label_box[1])
        label_x = x1 + _LABEL_PADDING_PX
        label_y = y1 + max(_LABEL_PADDING_PX, ((y2 - y1) - label_height) // 2)
        if label_x + label_width + _LABEL_PADDING_PX > x2:
            label_x = max(x1 + _LABEL_PADDING_PX, x2 - label_width - _LABEL_PADDING_PX)
        draw.text((label_x, label_y), label, fill=(255, 255, 255), font=font)

    def _save_pages(self, pages: list[Image.Image], input_path: Path, temp_dir: Path) -> Path:
        if not pages:
            raise OCRServiceError("Document contains no pages")

        rgb_pages = [page.convert("RGB") for page in pages]

        output_path = temp_dir / self._output_filename(input_path.name, input_path.suffix)
        if input_path.suffix.lower() == ".pdf" or len(rgb_pages) > 1:
            final_path = output_path.with_suffix(".pdf")
            rgb_pages[0].save(
                final_path,
                format="PDF",
                save_all=True,
                append_images=rgb_pages[1:],
            )
            return final_path

        output_format = self._image_format(input_path.suffix)
        final_path = output_path.with_suffix(input_path.suffix.lower())
        rgb_pages[0].save(final_path, format=output_format)
        return final_path

    def _output_filename(self, filename: str, suffix: str) -> str:
        source = Path(filename)
        ext = suffix.lower() if suffix else source.suffix.lower()
        return f"{source.stem}_redacted{ext}"

    def _image_format(self, suffix: str) -> str:
        return {
            ".png": "PNG",
            ".jpg": "JPEG",
            ".jpeg": "JPEG",
        }.get(suffix.lower(), "PNG")

    def _media_type(self, suffix: str, fallback: str) -> str:
        return {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(suffix.lower(), fallback or "application/octet-stream")


RedactionService._FIELD_LABELS = {
    **dict.fromkeys(RedactionService._PERSON_LABEL_KEYS, SensitiveDataType.PERSON),
    **dict.fromkeys(RedactionService._ADDRESS_LABEL_KEYS, SensitiveDataType.ADDRESS),
    **dict.fromkeys(RedactionService._CONTACT_LABEL_KEYS, SensitiveDataType.PHONE),
    **dict.fromkeys(RedactionService._EMAIL_LABEL_KEYS, SensitiveDataType.EMAIL),
    **dict.fromkeys(RedactionService._DATE_LABEL_KEYS, SensitiveDataType.DATE),
    **dict.fromkeys(
        RedactionService._ORGANIZATION_LABEL_KEYS,
        SensitiveDataType.ORGANIZATION,
    ),
    **RedactionService._ID_LABEL_KEYS,
}
