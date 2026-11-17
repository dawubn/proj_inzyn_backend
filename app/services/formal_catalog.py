"""Structural checks for classifier categories, not universal legal requirements.

The catalog is shared by extraction and built-in profiles. Optional/variant
sections are advisory: their absence must never make a document incomplete.
"""

from dataclasses import dataclass

from app.enums.document import DocumentType


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    pattern: str
    kind: str = "text"
    masks: tuple[str, ...] = ()
    min_words: int = 2
    advisory: bool = False


@dataclass(frozen=True)
class DocumentSpec:
    name: str
    fields: tuple[FieldSpec, ...]


def f(  # noqa: PLR0913 - declarative field configuration
    key: str,
    label: str,
    pattern: str,
    *,
    kind: str = "text",
    masks: tuple[str, ...] = (),
    min_words: int = 2,
    advisory: bool = False,
) -> FieldSpec:
    return FieldSpec("has_" + key, label, pattern, kind, masks, min_words, advisory)


EXTENDED_DOCUMENT_SPECS: dict[DocumentType, DocumentSpec] = {
    DocumentType.APPLICATION: DocumentSpec(
        "podanie o pracę lub CV",
        (
            f(
                "candidate",
                "Imię i nazwisko kandydata",
                r"imie i nazwisko|dane osobowe|kandydat|full name|name",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "contact",
                "Dane kontaktowe",
                r"dane kontaktowe|kontakt|telefon|e-?mail|contact|phone",
                masks=("telefon", "email", "adres"),
                min_words=1,
            ),
            f(
                "experience",
                "Doświadczenie zawodowe",
                r"doswiadczenie(?: zawodowe)?|przebieg pracy|work experience|employment history",
                kind="section",
            ),
            f(
                "education",
                "Wykształcenie",
                r"wyksztalcenie|education|qualifications",
                kind="section",
            ),
            f(
                "skills",
                "Umiejętności",
                r"umiejetnosci|kwalifikacje|skills",
                kind="section",
                advisory=True,
            ),
            f(
                "data_consent",
                "Zgoda na przetwarzanie danych",
                r"klauzula|zgoda na przetwarzanie|wyrazam zgode",
                kind="section",
                advisory=True,
            ),
        ),
    ),
    DocumentType.INVOICE: DocumentSpec(
        "faktura",
        (
            f(
                "invoice_number",
                "Numer faktury",
                r"(?:numer|nr\.?) faktury|faktura(?: vat)? nr\.?|invoice (?:number|no\.?)",
                kind="identifier",
            ),
            f(
                "issue_date",
                "Data wystawienia",
                r"data wystawienia(?: faktury)?|issue date|date of issue",
                kind="date",
                masks=("data",),
            ),
            f(
                "seller",
                "Sprzedawca",
                r"sprzedawca|dostawca|seller|supplier",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "buyer",
                "Nabywca",
                r"nabywca|kupujacy|buyer|customer",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "invoice_items",
                "Towary lub usługi",
                r"(?:nazwa|opis) (?:towaru lub uslugi|towaru|uslugi)"
                r"|pozycje(?: faktury)?|description|items",
                kind="section",
            ),
            f(
                "invoice_total",
                "Kwota należności",
                r"(?:kwota )?(?:razem brutto|do zaplaty|naleznosc ogolem)"
                r"|kwota brutto|total(?: due)?|amount due",
                kind="amount",
            ),
            f(
                "tax_details",
                "Rozliczenie podatku",
                r"(?:stawka|kwota) (?:vat|podatku)|vat|tax",
                kind="tax",
                advisory=True,
            ),
        ),
    ),
    DocumentType.PATENT: DocumentSpec(
        "dokument patentowy",
        (
            f(
                "invention_title",
                "Tytuł wynalazku",
                r"tytul(?: wynalazku)?|title(?: of (?:the )?invention)?",
                kind="title",
            ),
            f(
                "patent_applicant",
                "Zgłaszający lub uprawniony",
                r"zglaszajacy|uprawniony(?: z patentu)?|applicant|proprietor",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "invention_description",
                "Opis wynalazku",
                r"opis(?: wynalazku)?|description",
                kind="section",
            ),
            f(
                "patent_claims",
                "Zastrzeżenia patentowe",
                r"zastrzezenia(?: patentowe)?|claims",
                kind="section",
                advisory=True,
            ),
            f(
                "patent_abstract",
                "Skrót opisu",
                r"skrot(?: opisu)?|abstract",
                kind="section",
                advisory=True,
            ),
            f(
                "patent_number",
                "Numer zgłoszenia lub publikacji",
                r"numer (?:zgloszenia|publikacji|patentu)|application number|publication number",
                kind="identifier",
                advisory=True,
            ),
        ),
    ),
    DocumentType.DEMAND_LETTER: DocumentSpec(
        "wezwanie do zapłaty",
        (
            f(
                "creditor",
                "Wierzyciel",
                r"wierzyciel|creditor",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "debtor",
                "Dłużnik",
                r"dluznik|debtor",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "demand_amount",
                "Kwota do zapłaty",
                r"kwota(?: do zaplaty| zadluzenia| naleznosci)?|do zaplaty"
                r"|amount(?: due| outstanding)?",
                kind="amount",
            ),
            f(
                "payment_deadline",
                "Termin zapłaty",
                r"termin(?: zaplaty)?|payment deadline|deadline",
                min_words=1,
            ),
            f(
                "demand_date",
                "Data wezwania",
                r"data(?: wezwania| sporzadzenia)?|date",
                kind="date",
                masks=("data",),
            ),
            f(
                "signature",
                "Podpis wierzyciela lub pełnomocnika",
                r"podpis(?: wierzyciela)?|signature",
                kind="signature",
            ),
            f(
                "demand_basis",
                "Podstawa roszczenia",
                r"podstawa(?: roszczenia)?|tytul(?: naleznosci)?|basis of claim",
                advisory=True,
            ),
        ),
    ),
}
