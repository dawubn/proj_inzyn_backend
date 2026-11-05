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


_IDENTITY = (
    f(
        "surname",
        "Nazwisko",
        r"nazwisko(?! rodowe)|surname",
        kind="identity",
        masks=("person",),
        min_words=1,
    ),
    f(
        "given_names",
        "Imię lub imiona",
        r"imie(?: \(imiona\))?|imiona|given names?",
        kind="identity",
        masks=("person",),
        min_words=1,
    ),
    f(
        "birth_date",
        "Data urodzenia",
        r"data urodzenia|date of birth",
        kind="date",
        masks=("data",),
    ),
    f(
        "document_number",
        "Numer dokumentu",
        r"(?:seria i |seria oraz )?numer (?:dokumentu|dowodu(?: osobistego)?|paszportu)"
        r"|document (?:no\.?|number)|passport (?:no\.?|number)",
        kind="identifier",
        masks=("numer dokumentu",),
    ),
    f(
        "expiry_date",
        "Data ważności",
        r"data waznosci|wazny do|date of expiry|expiry date",
        kind="date",
        masks=("data",),
    ),
    f(
        "issuing_authority",
        "Organ wydający",
        r"organ wydajacy|wydany przez|authority|issuing authority",
        min_words=1,
    ),
    f(
        "document_image",
        "Zdjęcie i zabezpieczenia dokumentu",
        r"(?!)",
        kind="visual",
        advisory=True,
    ),
)

EXTENDED_DOCUMENT_SPECS: dict[DocumentType, DocumentSpec] = {
    DocumentType.APPLICATION: DocumentSpec(
        "wniosek",
        (
            f(
                "applicant",
                "Wnioskodawca",
                r"(?:dane )?wnioskodawc[ay]|applicant",
                kind="identity",
                masks=("person",),
            ),
            f(
                "recipient",
                "Adresat wniosku",
                r"adresat|organ administracji|do urzedu|recipient",
                min_words=1,
            ),
            f(
                "request",
                "Treść wniosku",
                r"tresc wniosku|wnosze o|wnioskuje o|prosze o|zadanie|request",
                kind="section",
            ),
            f(
                "application_date",
                "Data wniosku",
                r"data(?: sporzadzenia| zlozenia)?|date",
                kind="date",
                masks=("data",),
            ),
            f(
                "signature",
                "Podpis wnioskodawcy",
                r"podpis(?: wnioskodawcy)?|signature",
                kind="signature",
            ),
            f(
                "application_justification",
                "Uzasadnienie wniosku",
                r"uzasadnienie|justification",
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
    DocumentType.ID_CARD: DocumentSpec(
        "dowód osobisty",
        (
            *_IDENTITY,
            f("pesel", "Numer PESEL", r"(?:numer )?pesel", kind="pesel", masks=("pesel",)),
        ),
    ),
    DocumentType.PASSPORT: DocumentSpec(
        "paszport",
        (
            *_IDENTITY,
            f(
                "nationality",
                "Obywatelstwo",
                r"obywatelstwo|nationality",
                masks=("adres",),
                min_words=1,
            ),
        ),
    ),
    DocumentType.BANK_STATEMENT: DocumentSpec(
        "wyciąg bankowy",
        (
            f("bank", "Bank", r"(?:nazwa )?bank(?:u)?|bank name", min_words=1),
            f(
                "account_holder",
                "Posiadacz rachunku",
                r"posiadacz rachunku|wlasciciel rachunku|account holder",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "account_number",
                "Numer rachunku",
                r"(?:numer|nr\.?) (?:rachunku|konta)|iban|account number",
                kind="account",
                masks=("numer rachunku",),
            ),
            f(
                "statement_period",
                "Okres wyciągu",
                r"okres(?: wyciagu| rozliczeniowy)?|wyciag za okres|statement period|period",
                kind="period",
                masks=("data",),
            ),
            f(
                "opening_balance",
                "Saldo początkowe",
                r"saldo poczatkowe|opening balance",
                kind="amount",
            ),
            f("closing_balance", "Saldo końcowe", r"saldo koncowe|closing balance", kind="amount"),
            f(
                "transactions",
                "Historia operacji",
                r"(?:historia|lista|zestawienie) (?:operacji|transakcji)|operacje|transactions",
                kind="transactions",
            ),
        ),
    ),
    DocumentType.TAX_FORM: DocumentSpec(
        "deklaracja podatkowa",
        (
            f(
                "tax_form",
                "Oznaczenie formularza",
                r"formularz|rodzaj deklaracji|symbol formularza|form",
                kind="form_code",
            ),
            f(
                "taxpayer",
                "Podatnik",
                r"(?:dane )?podatnika?|taxpayer",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "tax_identifier",
                "Identyfikator podatnika",
                r"identyfikator podatkowy|nip(?: lub pesel)?|pesel|tax identification number",
                kind="tax_id",
                masks=("nip", "pesel"),
            ),
            f(
                "tax_period",
                "Okres rozliczenia",
                r"(?:rok|okres)(?: podatkowy| rozliczeniowy)?|tax year|tax period",
                kind="period",
                masks=("data",),
            ),
            f(
                "tax_office",
                "Urząd skarbowy",
                r"urzad skarbowy|adresat deklaracji|tax office",
                min_words=1,
            ),
            f(
                "tax_values",
                "Kwoty rozliczenia",
                r"podatek nalezny|kwota podatku|dochod|przychod|tax due|income",
                kind="amount",
            ),
        ),
    ),
    DocumentType.FINANCIAL_REPORT: DocumentSpec(
        "sprawozdanie finansowe",
        (
            f(
                "reporting_entity",
                "Jednostka sporządzająca sprawozdanie",
                r"(?:nazwa )?jednostk[ai]|nazwa podmiotu|spolka|entity|company",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "reporting_period",
                "Okres sprawozdawczy",
                r"okres sprawozdawczy|rok obrotowy|na dzien|reporting period|financial year",
                kind="period",
                masks=("data",),
            ),
            f(
                "financial_data",
                "Dane finansowe",
                r"aktywa(?: razem)?|pasywa(?: razem)?|przychody|wynik finansowy"
                r"|total assets|revenue",
                kind="amount",
            ),
            f("balance_sheet", "Bilans", r"bilans|balance sheet", kind="section", advisory=True),
            f(
                "income_statement",
                "Rachunek wyników",
                r"rachunek zyskow i strat|rachunek wynikow|income statement",
                kind="section",
                advisory=True,
            ),
            f(
                "financial_notes",
                "Informacja dodatkowa",
                r"informacja dodatkowa|dodatkowe informacje i objasnienia|notes",
                kind="section",
                advisory=True,
            ),
        ),
    ),
    DocumentType.GOVERNMENT_TENDER: DocumentSpec(
        "zamówienie publiczne",
        (
            f(
                "contracting_authority",
                "Zamawiający",
                r"zamawiajacy|nazwa zamawiajacego|contracting authority",
                kind="identity",
                masks=("person",),
                min_words=1,
            ),
            f(
                "tender_subject",
                "Przedmiot zamówienia",
                r"(?:opis )?przedmiot(?:u)? zamowienia|nazwa zamowienia"
                r"|subject of (?:the )?contract",
                kind="section",
            ),
            f(
                "tender_reference",
                "Numer postępowania",
                r"(?:numer|nr\.?) postepowania|znak sprawy|reference number",
                kind="identifier",
                advisory=True,
            ),
            f(
                "submission_deadline",
                "Termin składania ofert",
                r"termin skladania ofert|submission deadline|deadline",
                kind="date",
                masks=("data",),
                advisory=True,
            ),
            f(
                "submission_method",
                "Sposób składania ofert",
                r"(?:sposob|miejsce) skladania ofert|submission method",
                kind="section",
                advisory=True,
            ),
            f(
                "award_criteria",
                "Kryteria oceny ofert",
                r"kryteria(?: oceny ofert)?|award criteria",
                kind="section",
                advisory=True,
            ),
        ),
    ),
    DocumentType.LAW_AND_REGULATION: DocumentSpec(
        "akt prawny",
        (
            f("act_title", "Tytuł aktu", r"tytul(?: aktu)?|title", kind="title"),
            f("act_date", "Data aktu", r"data(?: aktu)?|z dnia|date", kind="date", masks=("data",)),
            f(
                "provisions",
                "Treść przepisów",
                r"art\.\s*\d+[a-z]?\.?|§\s*\d+\.?|przepisy|provisions|article \d+",
                kind="section",
            ),
            f(
                "publication_reference",
                "Oznaczenie publikacji",
                r"publikacja|dziennik ustaw|dz\.\s*u\.|publication reference",
                kind="identifier",
                advisory=True,
            ),
            f(
                "effective_date",
                "Wejście w życie",
                r"data wejscia w zycie|wejscie w zycie|entry into force",
                kind="section",
                advisory=True,
            ),
        ),
    ),
    DocumentType.MANUAL: DocumentSpec(
        "instrukcja",
        (
            f(
                "manual_subject",
                "Urządzenie lub temat instrukcji",
                r"(?:nazwa )?urzadzeni[ae]|produkt|model|tytul|product|device|title",
                kind="title",
            ),
            f(
                "operating_instructions",
                "Sposób obsługi",
                r"(?:instrukcja|sposob) obslugi|obsluga|uruchomienie|sposob uzycia"
                r"|operating instructions|operation|getting started",
                kind="section",
            ),
            f(
                "safety_instructions",
                "Informacje o bezpieczeństwie",
                r"bezpieczenstwo|ostrzezenia|safety(?: instructions)?|warnings",
                kind="section",
                advisory=True,
            ),
            f(
                "manual_version",
                "Wersja instrukcji",
                r"wersja(?: instrukcji)?|version|revision",
                kind="identifier",
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
    DocumentType.SCIENTIFIC_ARTICLE: DocumentSpec(
        "artykuł naukowy",
        (
            f("article_title", "Tytuł artykułu", r"tytul(?: artykulu)?|title", kind="title"),
            f("authors", "Autorzy", r"autor(?:zy)?|authors?", kind="identity", masks=("person",)),
            f(
                "article_content",
                "Treść artykułu",
                r"wprowadzenie|wstep|wyniki|dyskusja|metody(?:ka)?|introduction|results|discussion|methods",
                kind="section",
            ),
            f(
                "article_abstract",
                "Streszczenie",
                r"streszczenie|abstrakt|abstract",
                kind="section",
                advisory=True,
            ),
            f(
                "references",
                "Bibliografia",
                r"bibliografia|literatura|pismiennictwo|references",
                kind="section",
                advisory=True,
            ),
        ),
    ),
}
