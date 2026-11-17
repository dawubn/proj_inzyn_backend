"""Text rules for personal data, run per line on a lowercase copy without diacritics."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass

import phonenumbers

from app.enums.analysis import RedactionCategory

Span = tuple[int, int, str]

PERSON = "IMIE/NAZWISKO"
ADDRESS = "ADRES"
DATE = "DATA"
PESEL = "PESEL"
NIP = "NIP"
REGON = "REGON"
KRS = "KRS"
EMAIL = "EMAIL"
PHONE = "TELEFON"
DOCUMENT_NUMBER = "NUMER DOKUMENTU"
IDENTIFIER = "NUMER IDENTYFIKACYJNY"
ACCOUNT = "NUMER RACHUNKU"
CARD = "NUMER KARTY"
LAND_REGISTER = "NUMER KW"
VEHICLE = "NUMER REJESTRACYJNY"
SIGNATURE = "PODPIS"
PHOTO = "ZDJECIE"
CODE = "KOD"
HEALTH = "DANE MEDYCZNE"

_FOLD_TABLE = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def fold(text: str) -> str:
    """Lowercase ASCII-like copy with exactly the same length as the input."""
    folded = text.translate(_FOLD_TABLE)
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in folded)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _luhn(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        doubled = digit * 2 if index % 2 else digit
        total += doubled - 9 if doubled > 9 else doubled
    return total % 10 == 0


def _iban(value: str) -> bool:
    compact = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if not 15 <= len(compact) <= 34 or not compact[:2].isalpha():
        return False
    rotated = compact[4:] + compact[:4]
    number = "".join(str(int(c, 36)) for c in rotated)
    return int(number) % 97 == 1


def nip_valid(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if len(digits) != 10:
        return False
    weights = (6, 5, 7, 2, 3, 4, 5, 6, 7)
    return sum(d * w for d, w in zip(digits, weights, strict=False)) % 11 == digits[9]


def _has_digit(value: str) -> bool:
    return any(c.isdigit() for c in value)


@dataclass(frozen=True)
class PatternRule:
    label: str
    pattern: re.Pattern[str]
    check: Callable[[str], bool] | None = None
    # Text right before the match that makes it a reference number, not personal data.
    not_after: re.Pattern[str] | None = None
    group: int = 0


_NUMBERING = re.compile(
    r"(?:pkt|ust|§|section|clause|wersj\w*|version|v|nr|no|faktur\w*|invoice|art|par)\.?\s*$"
)
_REFERENCE = re.compile(
    r"(?:zamowien\w*|polis\w*|sprawy|faktur\w*|umowy|klienta|order|policy|invoice|contract)"
    r"\W*(?:nr|no|numer|number)?\.?\W*$"
)


_UPPER = "A-ZĄĆĘŁŃÓŚŹŻ"
_LOWER = "a-ząćęłńóśźż"
_WORD = rf"[{_UPPER}0-9][{_UPPER}{_LOWER}0-9'\u2019\-]*"
_HOUSE = r"\d{1,4}[a-zA-Z]?(?:\s?(?:/|m\.?|lok\.?|lokal|apt\.?)\s?\d{1,4}[a-zA-Z]?)?"
_PL_MONTHS = (
    r"stycz(?:nia|e[nń]|niu)|lut(?:ego|y|ym)|mar(?:ca|zec|cu)|kwie(?:tnia|ci[eę][nń]|tniu)"
    r"|maj(?:a|u|em)?|czerw(?:ca|iec|cu)|lip(?:ca|iec|cu)|sierp(?:nia|i[eę][nń]|niu)"
    r"|wrze(?:[sś]nia|si[eę][nń]|[sś]niu)|pa[zź]dziernik(?:a|u)?|listopad(?:a|zie)?"
    r"|grud(?:nia|zi[eę][nń]|niu)"
)
_ROMAN_MONTHS = r"XII|XI|IX|X|VIII|VII|VI|IV|V|III|II|I"
_ORDINALS = (
    r"pierwsz|drugi|trzeci|czwart|pi[aą]t|sz[oó]st|si[oó]dm|[oó]sm|dziewi[aą]t|dziesi[aą]t"
    r"|jedenast|dwunast|trzynast|czternast|pi[eę]tnast|szesnast|siedemnast|osiemnast"
    r"|dziewi[eę]tnast|dwudziest|trzydziest"
)
_EN_MONTHS = (
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)
_EN_MONTHS_FULL = (
    r"january|february|march|april|june|july|august|september|october|november|december"
)
_EN_STREET = (
    r"street|st|road|rd|avenue|ave|lane|ln|drive|dr|court|ct|way|close|boulevard|blvd|"
    r"place|pl|terrace|square|sq|crescent|gardens|hill|park|row|mews|grove"
)

# Longest digit patterns first, so a PESEL never claims part of an account number.
PATTERN_RULES: tuple[PatternRule, ...] = (
    PatternRule(DOCUMENT_NUMBER, re.compile(r"[A-Z0-9<]{5,}<<[A-Z0-9<]{10,}")),
    PatternRule(
        EMAIL,
        re.compile(
            r"(?<![\w.+\-])(?:[\w+\-]{1,40}\s?\.\s?){0,4}[\w+\-]{1,40}\s?(?:@|©|®|\(at\)|\[at\])"
            r"\s?[\w\-]{1,40}(?:\s?\.\s?[\w\-]{1,40}){0,4}\s?\.\s?[A-Za-z]{2,}\b"
        ),
    ),
    PatternRule(ACCOUNT, re.compile(r"(?<![\w])(?:[A-Z]{2}\s?)?\d{2}(?:\s?\d){24}(?!\d)")),
    PatternRule(
        ACCOUNT, re.compile(r"(?<![\w])[A-Z]{2}\d{2}(?:\s?[A-Z0-9]){11,30}(?![A-Z0-9])"), _iban
    ),
    PatternRule(CARD, re.compile(r"(?<!\d)\d(?:[ \-]?\d){12,18}(?!\d)"), _luhn),
    PatternRule(
        PESEL,
        re.compile(r"(?<![\d\-/.,+])\d(?:\s?\d){10}(?![\d\-/]|,\d|\s?(?:zł|zl|pln|eur|usd|gbp))"),
        not_after=_REFERENCE,
    ),
    PatternRule(
        NIP, re.compile(r"(?<![\d\-])(?:PL\s?)?\d{3}-\d{3}-\d{2}-\d{2}(?![\d\-])"), nip_valid
    ),
    PatternRule(
        NIP, re.compile(r"(?<![\d\-])(?:PL\s?)?\d{3}-\d{2}-\d{2}-\d{3}(?![\d\-])"), nip_valid
    ),
    PatternRule(LAND_REGISTER, re.compile(r"\b[A-Z]{2}[0-9][A-Z0-9]\s?/\s?\d{8}\s?/\s?\d\b")),
    PatternRule(IDENTIFIER, re.compile(r"(?<![\d\-])\d{3}-\d{2}-\d{4}(?![\d\-])")),
    PatternRule(
        IDENTIFIER, re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b")
    ),
    PatternRule(DOCUMENT_NUMBER, re.compile(r"\b[A-Z]{3}\s?(?:nr\.?\s)?\d{6}\b")),
    PatternRule(
        VEHICLE,
        re.compile(r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-Z])[A-HJ-NPR-Z0-9]{17}\b"),
    ),
    PatternRule(
        ADDRESS,
        re.compile(
            rf"\b(?i:ul|al|pl|os|ulica|ulicy|aleja|alei|plac|placu|osiedle|osiedlu)\.?\s?"
            rf"{_WORD}(?:\s{_WORD}){{0,3}}?,?\s?{_HOUSE}"
            rf"(?:,?\s?\d{{2}}-\d{{3}}(?:\s[{_UPPER}][{_LOWER}\-]+){{1,2}}"
            rf"|,\s[{_UPPER}][{_LOWER}\-]+(?:\s[{_UPPER}][{_LOWER}\-]+)?)?"
        ),
    ),
    PatternRule(
        ADDRESS,
        re.compile(
            rf"(?<![\d\-])\d{{2}}\s?-\s?\d{{3}}(?:\s[{_UPPER}][{_UPPER}{_LOWER}\-]+){{1,2}}"
        ),
    ),
    PatternRule(
        ADDRESS,
        # "w Zalesiu Górnym przy Leśnej 3": the place belongs to the address.
        re.compile(
            rf"\bw\s([{_UPPER}][{_LOWER}]+(?:\s[{_UPPER}][{_LOWER}]+)?\sprzy\s(?:ul\.?\s)?"
            rf"[{_UPPER}][{_LOWER}]+(?:\s[{_UPPER}][{_LOWER}]+)?\s{_HOUSE})"
        ),
        group=1,
    ),
    PatternRule(
        ADDRESS,
        re.compile(
            rf"\bprzy\s((?:ul\.?\s)?[{_UPPER}][{_LOWER}]+(?:\s[{_UPPER}][{_LOWER}]+)?\s{_HOUSE})"
        ),
        group=1,
    ),
    PatternRule(
        ADDRESS,
        re.compile(
            rf"(?i:skrytk\w*\s+pocztow\w*|p\.?\s?o\.?\s?box)(?:\s?nr\.?)?\s?\d+"
            rf"(?:,?\s?\d{{2}}-\d{{3}}(?:\s[{_UPPER}][{_LOWER}\-]+){{1,2}}"
            rf"|,\s[{_UPPER}][{_LOWER}]+(?:\s\d{{1,2}})?)?"
        ),
    ),
    PatternRule(
        ADDRESS,
        re.compile(
            rf"\b[{_UPPER}][{_LOWER}]+(?:straße|strasse|weg|gasse|platz|allee)\s\d+[a-z]?"
            rf"(?:,\s\d{{5}}\s[{_UPPER}][{_LOWER}]+)?"
            rf"|\b\d{{1,4}}(?:\sbis)?,?\s(?i:rue|avenue|boulevard|bd|via|calle|plaza)\s[^,\n]{{2,40}}?"
            rf",\s\d{{5}}\s[{_UPPER}][{_LOWER}]+"
        ),
    ),
    PatternRule(
        ADDRESS,
        # Street and number without "ul.", e.g. in a table cell: "Słoneczna 8/2, Tychy".
        re.compile(
            rf"\b(?!(?:Strona|Punkt|Załącznik|Art|Rozdział|Paragraf|Dział|Wydział|Tom|Poz|Page"
            rf"|Section|Chapter|Article|Część|Etap|Lp|Pozycja|Tabela|Rok|Krok)\b"
            rf"|(?i:{_PL_MONTHS}|{_EN_MONTHS})\b)"
            rf"[{_UPPER}][{_LOWER}]{{2,}}(?:\s[{_UPPER}][{_LOWER}]{{2,}}){{0,2}}\s\d{{1,4}}[a-z]?"
            rf"(?:\s?/\s?\d{{1,4}}|\s(?:m\.|lok\.)\s?\d{{1,4}})?,\s?(?:\d{{2}}-\d{{3}}\s)?"
            rf"[{_UPPER}][{_LOWER}\-]+"
        ),
    ),
    PatternRule(
        ADDRESS,
        re.compile(
            rf"(?:(?i:flat|unit|apartment|apt|suite)\.?\s?\w+,\s)?"
            rf"\b\d{{1,5}}(?:-\d{{1,5}})?[A-Z]?\s(?:[A-Z][a-z]+\s){{1,3}}(?i:{_EN_STREET})\b\.?"
            rf"(?:,?\s(?i:apt|flat|unit|suite)\.?\s?\w+)?"
            rf"(?:,\s[A-Z][a-z]+(?:\s[A-Z][a-z]+)?)?"
            rf"(?:,?\s(?:[A-Z]{{1,2}}\d[A-Z\d]?\s?\d[A-Z]{{2}}|[A-Z]{{2}}\s\d{{5}}(?:-\d{{4}})?))?"
        ),
    ),
    PatternRule(ADDRESS, re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s\d[A-Z]{2}\b")),
    PatternRule(
        DATE,
        re.compile(
            r"(?<![\d\-/])(?:\d{1,2}\s?[.\-/]\s?\d{1,2}\s?[.\-/]\s?(?:\d{4}|\d{2})"
            r"|\d{4}\s?[.\-/]\s?\d{1,2}\s?[.\-/]\s?\d{1,2})(?:\s?r\.?)?(?![\d\-/])"
        ),
        not_after=_NUMBERING,
    ),
    PatternRule(
        DATE,
        re.compile(
            rf"(?<![\w.])\d{{1,2}}\s?[.\s]\s?(?:{_ROMAN_MONTHS})\s?[.\s]\s?\d{{2,4}}(?:\s?r\.?)?"
        ),
    ),
    PatternRule(
        DATE,
        re.compile(
            rf"(?i)\b(?:(?:dwudziest|trzydziest)\w*\s)?(?:{_ORDINALS})\w*\s(?:{_PL_MONTHS})\b"
            rf"(?:\s(?:roku|r\.|tysi[aą]c\w*|dwa|dwu\w*|\w+set\w*|\w+dziest\w*|\w+dziesi\w*|\w+na[sś]t\w*"
            rf"|(?:{_ORDINALS})\w*)){{0,6}}"
        ),
    ),
    PatternRule(
        DATE, re.compile(rf"(?i)\b\d{{1,2}}(?:-?go)?\.?\s(?:{_PL_MONTHS})\s\d{{2,4}}(?:\s?r\.?)?")
    ),
    PatternRule(
        DATE, re.compile(rf"(?i)\b(?:{_PL_MONTHS}|{_EN_MONTHS_FULL})\s\d{{4}}(?:\s?r\.?)?")
    ),
    PatternRule(
        DATE,
        re.compile(
            rf"(?i)\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s(?:of\s)?(?:{_EN_MONTHS})\.?,?\s\d{{4}}"
            rf"|(?:{_EN_MONTHS})\.?\s\d{{1,2}}(?:st|nd|rd|th)?,?\s\d{{4}})\b"
        ),
    ),
)

_MONEY_AFTER = re.compile(
    r"^(?:,\d{2}\b|\s?(?:,\d{2})?\s?(?:zł|zl|pln|eur|usd|gbp|€|\$|£))", re.IGNORECASE
)
# Numbers that follow these words are amounts or references, not phone numbers.
_NOT_PHONE_CONTEXT = re.compile(
    r"(?:kwot\w*|wartos\w*|sum\w*|spraw\w*|faktur\w*|zamowieni\w*|umow\w*|regon|krs|nip"
    r"|polis\w*|klienta|amount|total|invoice|order|case|claim|policy)\W*(?:nr|no|numer|number)?\.?\W*$"
)
_MONEY_BEFORE = re.compile(r"(?:pln|eur|usd|gbp|€|\$|£)\s?$", re.IGNORECASE)


_VEHICLE_CONTEXT = re.compile(
    r"samoch\w*|pojazd\w*|\bauto\b|marki\b|ciagnik\w*|motocykl\w*|rejestracyj\w*|vehicle|\bcar\b"
)
_PLATE = re.compile(r"\b[A-Z]{2,3}\s?(?=[A-Z0-9]*\d)[A-Z0-9]{4,5}\b")


_PHONE_TRIES = 300


def _phone_spans(text: str, regions: tuple[str, ...]) -> list[Span]:
    spans: list[Span] = []
    seen: set[tuple[int, int]] = set()
    for region in regions:
        # A real line holds a few candidates; the cap keeps a page of digits from stalling.
        for match in phonenumbers.PhoneNumberMatcher(
            text, region, leniency=phonenumbers.Leniency.POSSIBLE, max_tries=_PHONE_TRIES
        ):
            start, end = match.start, match.end
            raw = text[start:end]
            if len(_digits(raw)) < 9 and not raw.lstrip().startswith("+"):
                continue
            if (start, end) in seen:
                continue
            if start > 1 and text[start - 1] in "-/" and text[start - 2].isalnum():
                continue  # part of a reference such as KB-2026-000123
            if _MONEY_AFTER.match(text[end : end + 6]) or _MONEY_BEFORE.search(
                text[max(0, start - 4) : start]
            ):
                continue
            if _NOT_PHONE_CONTEXT.search(fold(text[max(0, start - 25) : start])):
                continue
            seen.add((start, end))
            spans.append((start, end, PHONE))
    return spans


def _overlaps(span: tuple[int, int], taken: list[Span]) -> bool:
    return any(span[0] < end and span[1] > start for start, end, _ in taken)


def _line_pattern_spans(line: str) -> list[Span]:
    spans: list[Span] = []
    folded = fold(line)
    for rule in PATTERN_RULES:
        for match in rule.pattern.finditer(line):
            start, end = match.span(rule.group)
            if rule.check is not None and not rule.check(match.group(rule.group)):
                continue
            if rule.not_after is not None and rule.not_after.search(
                folded[max(0, start - 25) : start]
            ):
                continue
            same = [
                i for i, s in enumerate(spans) if s[2] == rule.label and s[0] < end and s[1] > start
            ]
            if same:
                # Two shapes of one value, e.g. street with number and postcode with city.
                index = same[0]
                spans[index] = (min(spans[index][0], start), max(spans[index][1], end), rule.label)
                continue
            if _overlaps((start, end), spans):
                continue
            spans.append((start, end, rule.label))
    if _VEHICLE_CONTEXT.search(folded):
        for match in _PLATE.finditer(line):
            if not _overlaps(match.span(), spans):
                spans.append((match.start(), match.end(), VEHICLE))
    for span in _phone_spans(line, ("PL", "GB", "US")):
        if not _overlaps(span[:2], spans):
            spans.append(span)
    # Dates are matched only so that no other rule takes their digits; they stay visible.
    return [s for s in spans if s[2] != DATE]


def find_pattern_spans(text: str) -> list[Span]:
    """Identifiers, contacts and addresses recognised by their shape."""
    spans: list[Span] = []
    offset = 0
    for line in text.split("\n"):
        spans.extend((offset + a, offset + b, label) for a, b, label in _line_pattern_spans(line))
        offset += len(line) + 1
    return spans


# Field captions with the value after them or on the next line, matched on folded text.
_PERSON_CAPTIONS = (
    r"imie i nazwisko|nazwisko i imie|imiona? i nazwisko|nazwisko(?: rodowe)?|imiona?"
    r"|imie (?:ojca|matki)|nazwisko rodowe matki"
    r"|powod(?:ka)?|pozwan[ya]|wnioskodawc[ay]|uczestni(?:k|czka)(?: postepowania)?"
    r"|dluzni(?:k|czka)|wierzyciel(?:ka)?|najemc[ay]|wynajmujac[ya]|mocodawc[ay]"
    r"|pelnomocni(?:k|czka)|zleceniodawc[ay]|zleceniobiorc[ay]|pracowni(?:k|czka)"
    r"|pracodawca|nabywc[ay]|sprzedawc[ay]|kupujac[ya]|sprzedajac[ya]|darczyn(?:ca|czyni)"
    r"|obdarowan[ya]|swiade(?:k)|osoba kontaktowa|posiadacz(?: rachunku)?|wlasciciel(?:ka)?"
    r"|zamawiajac[ya]|wykonawca|uzytkowni(?:k|czka)|klient(?:ka)?|pacjent(?:ka)?|autor(?:zy)?"
    r"|name|full name|first name|given names?|surname|last name|family name"
    r"|plaintiff|defendant|claimant|respondent|applicant|tenant|landlord|employee"
    r"|employer|buyer|seller|purchaser|principal|agent|attorney|witness|account holder"
    r"|patient|beneficiary|client|customer|guarantor|borrower|lender"
)
_PERSON_CUES = (
    r"nizej podpisan[ya](?:\s?/\s?a)?|ustanawiam pelnomocnikiem|ustanawiam pelnomocniczka"
    r"|udzielam pelnomocnictwa(?: panu| pani)?|ja,"
)
_HONORIFICS = (
    r"(?:pan|pana|pani|panu|pania|panem|panstwa|panstwu|panow|paniom|mr|mrs|ms|miss|dr|prof)\.?"
    # Abbreviated professional titles are written with a period; "ADW OKACKA" is a misread word.
    r"|(?:adw|mec|r\.\s?pr)\."
)
_ADDRESS_CAPTIONS = (
    r"adres(?: zamieszkania| zameldowania| do korespondencji| do doreczen)?"
    r"|miejsce zamieszkania|zamieszkal[ya](?: przy| w)?|zamieszkalego(?: przy| w)?"
    r"|zamieszkalej(?: przy| w)?|zam\.(?: przy| w)?|miejsce urodzenia|urodzon[ya] w"
    r"|address|home address|home|residing at|resident at|place of birth"
)
_NUMBER_CAPTIONS: tuple[tuple[str, str], ...] = (
    (r"pesel", PESEL),
    (r"nip|vat (?:number|no\.?|id|reg\w*)|tax id|tin|ein|identyfikator podatkowy", NIP),
    (r"regon", REGON),
    (r"krs|company number|company no\.?", KRS),
    (
        (
            r"(?:numer|nr\.?) rej(?:estracyjn\w*|\.)?|numerze rejestracyjnym|vin"
            r"|registration(?: number| no\.?)?"
        ),
        VEHICLE,
    ),
    (
        (
            r"(?:seria i )?(?:numer|nr\.?) (?:dowodu(?: osobistego)?|dokumentu(?: tozsamosci)?"
            r"|paszportu|prawa jazdy)|dowod(?:em)? osobist\w*|dokument\w* tozsamosci|praw[oa] jazdy"
            r"|o numerze(?! rej)|paszport(?: nr\.?)?|passport(?: no\.?| number)?|id (?:no\.?|number)"
            r"|driving licen[cs]e(?: no\.?| number)?|driver'?s license(?: no\.?)?"
        ),
        DOCUMENT_NUMBER,
    ),
    (
        (
            r"ssn|social security(?: number| no\.?)?|national insurance(?: number| no\.?)?|nino"
            r"|ni (?:number|no\.?)"
        ),
        IDENTIFIER,
    ),
    (
        (
            r"(?:numer|nr\.?) (?:rachunku|konta)(?: bankowego)?|rachunek(?: bankowy)?(?: nr\.?)?"
            r"|konto(?: nr\.?)?|iban|account(?: number| no\.?)?|sort code"
        ),
        ACCOUNT,
    ),
    (r"karta(?: nr\.?)?|card(?: number| no\.?)?", CARD),
    (r"tel\.?(?: kom\.?)?|telefon\w*|phone|mobile|tel\. no\.?|fax", PHONE),
    (r"e-?mail|adres e-?mail|adres elektroniczny", EMAIL),
    (r"ksieg\w* wieczyst\w*(?: nr\.?)?|kw(?: nr\.?)?|nr kw", LAND_REGISTER),
)


# Whole words only, so surnames such as Sadowski or Bankiewicz are not taken for companies.
_COMPANY = re.compile(
    r"\b(?:sp\.?\s?z\s?[o0]\.?\s?[o0]|s\.?\s?a\.|sp\.\s?[jkp]\.?|spolk[aiey]|firm[aey]|fundacj[aie]"
    r"|stowarzyszeni[ae]|ltd|plc|inc|llc|gmbh|corp|company|limited|urzad|urzedu|sad|sadu"
    r"|bank|banku|gmina|gminy|starostwo|ministerstwo|fundusz|funduszu|uczelnia|szkola|szkoly"
    r"|skarb panstwa|przedsiebiorstw\w*|grupa|spoldzielni\w*|wspolnot\w*|prezydent\w*"
    r"|zaklad\w*|agencj\w*|instytut\w*|kancelari\w*|towarzystw\w*|izba|izby)\b"
)
# Job titles written with capitals, e.g. "Inspektor Ochrony Danych", are not names.
_OFFICE = re.compile(
    r"(?:inspektor|kierownik|dyrektor|prezes|wiceprezes|czlon\w*|zarzad|ksiegow|specjalist"
    r"|koordynator|referent|naczelnik|sekretarz|administrator|pelnomocnik zarzadu|glown\w*"
    r"|starszy|mlodszy|asystent|manager|menedzer|officer|director|head of)\w*\b"
)
_FILLER = re.compile(r"^[\s.,:;_\-…·•()/*]*$")
_NOT_A_VALUE = re.compile(
    r"^(?:nie dotyczy|brak|n/?a|none|do uzupelnienia|dane zostana uzupelnione|"
    r"wpisz|podaj|uzupelnij|zgodnie z|jak wyzej|patrz|as above|see above|same as|as stated)\b"
)
_STOP_AFTER_VALUE = re.compile(
    r",\s*(?=(?:pesel|nip|regon|krs|zam\b|zamieszka|ur\.|urodzon|legitymuj|tel|e-?mail|adres"
    r"|zwan|prowadz|nr\b|numer|dowod|dzialaj|reprezentowan|w imieniu|born|residing|of\b|"
    r"phone|email|passport|date of birth|ssn|national insurance))"
    r"|\s+(?=(?:zwan[yaye]|zam\.|pesel\b|nip\b|legitymuj|prowadzac|dzialajac|reprezentowan))"
)
_LABEL_WORDS = re.compile(r"pesel|nip|regon|krs|tel|telefon|e-?mail|zam|nr|iban|ssn")


def is_label_word(text: str) -> bool:
    """Captions that must stay visible even when a detector span covers them."""
    clean = fold(text).strip(" .,:;()[]")
    if not clean:
        return False
    if _LABEL_WORDS.fullmatch(clean):
        return True
    return bool(
        re.fullmatch(
            rf"(?:{_PERSON_CAPTIONS}|{_ADDRESS_CAPTIONS}|{_NUMBER_CAPTIONS_ALL}"
            rf"|podpis\w*|signature|signed(?: by)?|date|data)",
            clean,
        )
    )


_NUMBER_CAPTIONS_ALL = "|".join(pattern for pattern, _ in _NUMBER_CAPTIONS)

_CAPTION_RE = re.compile(
    rf"(?:^|(?<=[\s,;(]))(?P<cap>{_NUMBER_CAPTIONS_ALL}|{_ADDRESS_CAPTIONS}|{_PERSON_CAPTIONS})"
    rf"(?![a-z0-9])[ \t]?(?P<colon>[:\-](?!\d)|\.(?=\s))?[ \t]*"
)
_CUE_RE = re.compile(rf"(?:^|(?<=[\s,;(]))(?P<cap>{_PERSON_CUES})(?![a-z0-9])[ \t]+")
# Runs on folded text (no diacritics), so "Panią" and "Państwa" match as well.
_HONORIFIC_RE = re.compile(rf"(?:^|(?<=[\s,;(]))(?:{_HONORIFICS})\s+", re.IGNORECASE)
_NAME_RUN = re.compile(
    rf"[{_UPPER}][{_UPPER}{_LOWER}'\u2019\-]+\.?(?:\s[{_UPPER}][{_UPPER}{_LOWER}'\u2019\-]*\.?){{0,3}}"
)


def _caption_label(caption: str) -> str:
    folded = fold(caption)
    if re.fullmatch(_ADDRESS_CAPTIONS, folded):
        return ADDRESS
    for pattern, label in _NUMBER_CAPTIONS:
        if re.fullmatch(pattern, folded):
            return label
    return PERSON


_INLINE_ADDRESS = re.compile(r"zam|resid|urodzon|place of birth|miejsce")
_CAPTION_AT_END = re.compile(
    rf"(?:^|(?<=[\s,;(]))(?P<cap>{_PERSON_CAPTIONS}|{_ADDRESS_CAPTIONS}|{_NUMBER_CAPTIONS_ALL})$"
)


def caption_label(text: str) -> str | None:
    """Label of a personal field whose caption ends this text, e.g. "Adres zamieszkania"."""
    folded = fold(text).strip().rstrip(":").strip()
    match = _CAPTION_AT_END.search(folded)
    return _caption_label(match.group("cap")) if match else None


def _value_end(line: str, start: int) -> int:
    match = _STOP_AFTER_VALUE.search(fold(line), start)
    return match.start() if match else len(line.rstrip())


# Captions that only ever hold a name; other person captions are party roles.
_IDENTITY_CAPTION = re.compile(
    r"imie i nazwisko|nazwisko i imie|imiona? i nazwisko|nazwisko(?: rodowe)?|imiona?"
    r"|imie (?:ojca|matki)|nazwisko rodowe matki|name|full name|first name|given names?|surname"
    r"|last name|family name"
)
_TITLE_PREFIX = re.compile(
    r"\s*(?:(?:adw|r\.\s?pr|mec|dr|prof|pan|pani|mr|mrs|ms|miss|radca prawny|adwokat)\.?\s+)+"
)
_MIN_NAME_WORDS = 2

_NUMBER_FILLERS = {"serii", "seria", "nr", "numer", "numerze", "no", "o", "ue", "eu"}


def _number_value(line: str, start: int) -> tuple[int, int] | None:
    """Run of tokens that look like an identifier, e.g. "QQ 12 34 56 C" or "PL 61 1090"."""
    pos, end, first = start, None, None
    for token in re.finditer(r"\S+", line[start:]):
        word = token.group(0).rstrip(".,;")
        if not word:
            break
        if fold(word) in _NUMBER_FILLERS:
            continue
        if first is None:
            first = start + token.start()
        is_code = _has_digit(word) or (len(word) <= 4 and word.isalnum() and word.isupper())
        if not is_code and not re.fullmatch(r"[<\-/()+]+", word):
            break
        end = start + token.start() + len(word)
        pos = end
        if token.group(0).endswith((",", ";")):
            break
    if end is None or first is None or not _has_digit(line[first:end]):
        return None
    return first, pos


def _person_value(line: str, start: int, end: int) -> tuple[int, int] | None:
    value = line[start:end]
    folded = fold(value).strip()
    if not folded or _FILLER.match(value) or _NOT_A_VALUE.match(folded):
        return None
    if _COMPANY.search(folded):
        return None
    if not re.search(rf"[{_UPPER}{_LOWER}]{{2,}}", value):
        return None
    return start, end


def find_caption_spans(lines: list[str]) -> list[Span]:
    """Values written after field captions, offsets relative to "\\n".join(lines)."""
    spans: list[Span] = []
    offset = 0
    pending: str | None = None
    for line in lines:
        folded = fold(line)
        if pending is not None and line.strip() and not _CAPTION_RE.match(folded.strip()):
            found = _next_line_value(line, pending)
            if found:
                spans.append((offset + found[0], offset + found[1], pending))
        pending = None
        for match in _CAPTION_RE.finditer(folded):
            label = _caption_label(match.group("cap"))
            colon = match.group("colon") is not None
            # A role followed by a period ends a sentence, it is not a form field.
            if label == PERSON and match.group("colon") == ".":
                continue
            # Without a colon a person caption is usually a title or a word in a sentence.
            if not colon and (
                label == PERSON
                or (
                    match.start() > 0
                    and label == ADDRESS
                    and not _INLINE_ADDRESS.match(match.group("cap"))
                )
            ):
                continue
            start = match.end()
            if not line[start:].strip():
                if colon or match.start() == 0:
                    pending = label
                continue
            found = _caption_value(line, start, label, caption=match.group("cap"))
            if found:
                spans.append((offset + found[0], offset + found[1], label))
        for match in _CUE_RE.finditer(folded):
            run = _name_run(line, match.end())
            if run:
                spans.append((offset + run[0], offset + run[1], PERSON))
        for match in _HONORIFIC_RE.finditer(folded):
            run = _name_run(line, match.end())
            if run:
                spans.append((offset + run[0], offset + run[1], PERSON))
        offset += len(line) + 1
    return spans


def _name_run(line: str, start: int) -> tuple[int, int] | None:
    run = _NAME_RUN.match(line, start)
    if not run:
        return None
    value = run.group(0)
    if _COMPANY.search(fold(value)) or all(is_label_word(w) for w in value.split()):
        return None
    return run.start(), run.end()


def _next_line_value(line: str, label: str) -> tuple[int, int] | None:
    start = len(line) - len(line.lstrip())
    if label == PERSON:
        run = _name_run(line, start)
        # Only a line that is nothing but a name, not the next sentence of the text.
        if run and re.fullmatch(r"\s*(?:[,;].*)?", line[run[1] :]):
            return run
        return None
    if label == ADDRESS and not _has_digit(line):
        return None
    if label == EMAIL and not re.search(r"[@©®]", line):
        return None
    return _caption_value(line, start, label)


def _role_value(line: str, start: int) -> tuple[int, int] | None:
    """After a party role only a name counts, not a clause that happens to follow a colon."""
    title = _TITLE_PREFIX.match(fold(line), start)
    position = title.end() if title else start
    run = _name_run(line, position)
    if run is None or len(line[run[0] : run[1]].split()) < _MIN_NAME_WORDS:
        return None
    if _OFFICE.match(fold(line), run[0]):
        return None
    if _COMPANY.search(fold(line[start : _value_end(line, start)])):
        return None
    return run


def _caption_value(line: str, start: int, label: str, caption: str = "") -> tuple[int, int] | None:
    if label == PERSON and not _IDENTITY_CAPTION.fullmatch(caption):
        return _role_value(line, start)
    if label in (PERSON, ADDRESS):
        end = _value_end(line, start)
        if label == PERSON:
            return _person_value(line, start, end)
        value = line[start:end]
        if _FILLER.match(value) or _NOT_A_VALUE.match(fold(value).strip()):
            return None
        return (start, end) if re.search(r"\w", value) else None
    if label == EMAIL:
        end = _value_end(line, start)
        comma = re.search(r"[,;]\s", line[start:end])
        end = start + comma.start() if comma else end
        value = line[start:end]
        return (start, end) if re.search(r"\w", value) and not _FILLER.match(value) else None
    return _number_value(line, start)


# Polish inflection endings stripped when comparing name forms; a stem keeps at least 3 letters.
_NAME_ENDING = re.compile(
    r"(?<=[a-z]{3})(?:iego|iemu|owie|iem|ego|emu|owi|ami|ach|iej|ej|im|ym|em|om|ie|a|y|i|u|e|o)$"
)
# Forms of address and professional titles written before a name. Words that are also
# surnames, such as Starosta, Wojewoda or Sąd, are left out on purpose.
_TITLES = (
    r"pan|pani|panu|pania|panem|panstw|mr|mrs|ms|miss|dr|prof|adw|mec|adwokat|adwokac|radc"
    r"|mecenas|szanown|solicitor|barrister|attorney|ssr|sso|ssa|sekr|st"
)
_TITLE_WORD = re.compile(_TITLES)
# Stems of roles, offices and other capitalised words that are never a surname.
_NOT_NAME = re.compile(
    rf"{_TITLES}|sad|powod|pozwan|najemc|wynajmujac|mocodawc|pelnomocnik|pelnomocnictw|umow"
    r"|dokument|kancelari|zleceniodawc|zleceniobiorc|wnioskodawc|starost|wojewod|burmistrz"
    r"|prezydent|marszalk|sedzi|sedzia|prokurator|komornik|notariusz|podpis|utajnion|tenant"
    r"|landlord|claimant|defendant|plaintiff|court|witness|client|employee|signed|signature"
)


def is_title_word(token: str) -> bool:
    """A form of address or a professional title such as Pan, Adwokat or SSR, not a name."""
    clean = re.sub(r"[^a-z]", "", fold(token))
    if clean == "sad" and token.rstrip().endswith("."):
        return True  # "st. sekr. sąd." before a court clerk's name
    return len(clean) >= 2 and bool(
        _TITLE_WORD.fullmatch(clean) or _TITLE_WORD.fullmatch(_raw_stem(clean))
    )


def name_stem(token: str) -> str | None:
    """Comparable stem of a name form: Kowalskiego, Kowalskim and KOWALSKI share one."""
    clean = re.sub(r"[^a-z]", "", fold(token).replace("'", ""))
    if len(clean) < 3:
        return None
    clean = _raw_stem(clean)
    if _NOT_NAME.fullmatch(clean):
        return None
    return clean


def _raw_stem(clean: str) -> str:
    ending = _NAME_ENDING.search(clean)
    if ending:
        clean = clean[: ending.start()]
    # Lewandowscy and Lewandowski, Marek and Marka, Paweł and Pawła share a stem.
    if clean.endswith(("sc", "dz")) and len(clean) > 4:
        clean = clean[:-1] + ("k" if clean.endswith("c") else "g")
    if re.search(r"[^aeiouy](?:ek|el)$", clean) and len(clean) > 4:
        clean = clean[:-2] + clean[-1]
    return clean


def name_parts(text: str) -> list[str]:
    """Words of a person span split on spaces and hyphens, without initials."""
    parts = re.split(r"[\s\-]+", text)
    return [p for p in parts if len(re.sub(r"\W", "", p)) >= 3]


# Kind of data that a user can leave out of anonymization, per label
_CATEGORY_OF_LABEL: dict[str, RedactionCategory] = {
    PERSON: RedactionCategory.PERSON,
    ADDRESS: RedactionCategory.ADDRESS,
    DATE: RedactionCategory.DATE,
    PESEL: RedactionCategory.PESEL,
    EMAIL: RedactionCategory.EMAIL,
    PHONE: RedactionCategory.PHONE,
}


def label_selected(label: str, categories: Collection[RedactionCategory] | None) -> bool:
    """Whether data with this label is anonymized for the chosen kinds of data.

    None means all kinds. Labels without a choosable kind (e.g. NIP, account numbers,
    signatures) are always anonymized.
    """
    if categories is None:
        return True
    category = _CATEGORY_OF_LABEL.get(label)
    return category is None or category in categories


_SCRUB_LABELS = (EMAIL, ACCOUNT, CARD, PESEL, NIP, DOCUMENT_NUMBER, IDENTIFIER, PHONE)


def scrub_text(
    text: str,
    name_stems: set[str] | None = None,
    categories: Collection[RedactionCategory] | None = None,
) -> tuple[str, int]:
    """Replace identifiers and known names that reached the cloud OCR text.

    With `categories` only data of the chosen kinds is replaced, see label_selected.
    """
    spans = [
        s
        for s in find_pattern_spans(text)
        if s[2] in _SCRUB_LABELS and label_selected(s[2], categories)
    ]
    if name_stems and label_selected(PERSON, categories):
        for match in re.finditer(rf"[{_UPPER}][{_UPPER}{_LOWER}'\u2019]+", text):
            stem = name_stem(match.group(0))
            if stem and stem in name_stems and not _overlaps(match.span(), spans):
                spans.append((match.start(), match.end(), PERSON))
    result = text
    for start, end, label in sorted(spans, reverse=True):
        result = f"{result[:start]}[UTAJNIONO: {label}]{result[end:]}"
    return result, len(spans)
