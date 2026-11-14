"""Document definitions used by the synthetic benchmark.

Independent from application imports.
Optional, signature and visual fields are excluded from missing-field scoring.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from .models import DefectKind, FieldDefinition


def field(
    key: str,
    label: str,
    kind: str,
    advisory: bool = False,
) -> FieldDefinition:
    return FieldDefinition(
        key=key,
        label=label,
        kind=kind,
        advisory=advisory,
    )


CATALOG: Mapping[str, tuple[FieldDefinition, ...]] = MappingProxyType(
    {
        "application": (
            field("has_applicant", "Wnioskodawca", "identity"),
            field("has_recipient", "Adresat wniosku", "text"),
            field("has_request", "Treść wniosku", "section"),
            field("has_application_date", "Data sporządzenia", "date"),
            field("has_signature", "Podpis wnioskodawcy", "signature"),
            field(
                "has_application_justification",
                "Uzasadnienie wniosku",
                "section",
                advisory=True,
            ),
        ),
        "invoice": (
            field("has_invoice_number", "Numer faktury", "identifier"),
            field("has_issue_date", "Data wystawienia", "date"),
            field("has_seller", "Sprzedawca", "identity"),
            field("has_buyer", "Nabywca", "identity"),
            field("has_invoice_items", "Nazwa towaru lub usługi", "section"),
            field("has_invoice_total", "Do zapłaty", "amount"),
            field("has_tax_details", "Stawka VAT", "tax", advisory=True),
        ),
        "id_card": (
            field("has_surname", "Nazwisko", "identity"),
            field("has_given_names", "Imiona", "identity"),
            field("has_birth_date", "Data urodzenia", "date"),
            field("has_document_number", "Numer dokumentu", "identifier"),
            field("has_expiry_date", "Data ważności", "date"),
            field("has_issuing_authority", "Organ wydający", "text"),
            field(
                "has_document_image",
                "Zdjęcie i zabezpieczenia dokumentu",
                "visual",
                advisory=True,
            ),
            field("has_pesel", "Numer PESEL", "pesel"),
        ),
        "passport": (
            field("has_surname", "Nazwisko", "identity"),
            field("has_given_names", "Imiona", "identity"),
            field("has_birth_date", "Data urodzenia", "date"),
            field("has_document_number", "Numer dokumentu", "identifier"),
            field("has_expiry_date", "Data ważności", "date"),
            field("has_issuing_authority", "Organ wydający", "text"),
            field(
                "has_document_image",
                "Zdjęcie i zabezpieczenia dokumentu",
                "visual",
                advisory=True,
            ),
            field("has_nationality", "Obywatelstwo", "text"),
        ),
        "bank_statement": (
            field("has_bank", "Bank", "text"),
            field("has_account_holder", "Posiadacz rachunku", "identity"),
            field("has_account_number", "Numer rachunku", "account"),
            field("has_statement_period", "Okres wyciągu", "period"),
            field("has_opening_balance", "Saldo początkowe", "amount"),
            field("has_closing_balance", "Saldo końcowe", "amount"),
            field("has_transactions", "Historia operacji", "transactions"),
        ),
        "tax_form": (
            field("has_tax_form", "Oznaczenie formularza", "form_code"),
            field("has_taxpayer", "Podatnik", "identity"),
            field("has_tax_identifier", "NIP", "tax_id"),
            field("has_tax_period", "Rok podatkowy", "period"),
            field("has_tax_office", "Urząd skarbowy", "text"),
            field("has_tax_values", "Podatek należny", "amount"),
        ),
        "financial_report": (
            field("has_reporting_entity", "Jednostka", "identity"),
            field("has_reporting_period", "Okres sprawozdawczy", "period"),
            field("has_financial_data", "Aktywa razem", "amount"),
            field("has_balance_sheet", "Bilans", "section", advisory=True),
            field(
                "has_income_statement",
                "Rachunek wyników",
                "section",
                advisory=True,
            ),
            field(
                "has_financial_notes",
                "Informacja dodatkowa",
                "section",
                advisory=True,
            ),
        ),
        "government_tender": (
            field("has_contracting_authority", "Zamawiający", "identity"),
            field("has_tender_subject", "Przedmiot zamówienia", "section"),
            field(
                "has_tender_reference",
                "Numer postępowania",
                "identifier",
                advisory=True,
            ),
            field(
                "has_submission_deadline",
                "Termin składania ofert",
                "date",
                advisory=True,
            ),
            field(
                "has_submission_method",
                "Sposób składania ofert",
                "section",
                advisory=True,
            ),
            field(
                "has_award_criteria",
                "Kryteria oceny ofert",
                "section",
                advisory=True,
            ),
        ),
        "law_and_regulation": (
            field("has_act_title", "Tytuł aktu", "title"),
            field("has_act_date", "Data aktu", "date"),
            field("has_provisions", "Przepisy", "section"),
            field(
                "has_publication_reference",
                "Publikacja",
                "identifier",
                advisory=True,
            ),
            field(
                "has_effective_date",
                "Wejście w życie",
                "section",
                advisory=True,
            ),
        ),
        "manual": (
            field("has_manual_subject", "Urządzenie", "title"),
            field("has_operating_instructions", "Sposób obsługi", "section"),
            field(
                "has_safety_instructions",
                "Informacje o bezpieczeństwie",
                "section",
                advisory=True,
            ),
            field(
                "has_manual_version",
                "Wersja instrukcji",
                "identifier",
                advisory=True,
            ),
        ),
        "patent": (
            field("has_invention_title", "Tytuł wynalazku", "title"),
            field("has_patent_applicant", "Zgłaszający", "identity"),
            field("has_invention_description", "Opis wynalazku", "section"),
            field(
                "has_patent_claims",
                "Zastrzeżenia patentowe",
                "section",
                advisory=True,
            ),
            field(
                "has_patent_abstract",
                "Skrót opisu",
                "section",
                advisory=True,
            ),
            field(
                "has_patent_number",
                "Numer zgłoszenia lub publikacji",
                "identifier",
                advisory=True,
            ),
        ),
        "scientific_article": (
            field("has_article_title", "Tytuł artykułu", "title"),
            field("has_authors", "Autorzy", "identity"),
            field("has_article_content", "Wprowadzenie", "section"),
            field(
                "has_article_abstract",
                "Streszczenie",
                "section",
                advisory=True,
            ),
            field("has_references", "Bibliografia", "section", advisory=True),
        ),
        "power_of_attorney": (
            field("has_date", "Data sporządzenia", "date"),
            field("has_principal_section", "Mocodawca", "identity"),
            field("has_attorney_section", "Pełnomocnik", "identity"),
            field(
                "has_authorization_scope_section",
                "Zakres pełnomocnictwa",
                "section",
            ),
            field("has_signature", "Podpis", "signature"),
        ),
        "contract": (
            field("has_date", "Data zawarcia", "date"),
            field("has_parties_section", "Strony umowy", "parties"),
            field("has_subject_section", "Przedmiot umowy", "section"),
            field("has_signature", "Podpis", "signature"),
        ),
        "lawsuit": (
            field("has_date", "Data sporządzenia", "date"),
            field("has_court_section", "Sąd", "text"),
            field("has_parties_section", "Strony postępowania", "parties"),
            field("has_claim_section", "Żądanie pozwu", "section"),
            field("has_justification_section", "Uzasadnienie", "section"),
            field("has_signature", "Podpis", "signature"),
        ),
    }
)


TITLES: Mapping[str, str] = MappingProxyType(
    {
        "application": "WNIOSEK O WYDANIE ZAŚWIADCZENIA",
        "invoice": "FAKTURA VAT",
        "id_card": "KARTA DANYCH DOWODU OSOBISTEGO — WZÓR TESTOWY",
        "passport": "KARTA DANYCH PASZPORTU — WZÓR TESTOWY",
        "bank_statement": "WYCIĄG BANKOWY",
        "tax_form": "DEKLARACJA PODATKOWA PIT-37 — PRZYKŁAD TESTOWY",
        "financial_report": "SPRAWOZDANIE FINANSOWE",
        "government_tender": "OGŁOSZENIE O ZAMÓWIENIU",
        "law_and_regulation": ("REGULAMIN UDOSTĘPNIANIA SAL — FIKCYJNY AKT NORMATYWNY"),
        "manual": "INSTRUKCJA OBSŁUGI CZUJNIKA",
        "patent": "OPIS WYNALAZKU — DOKUMENT TESTOWY",
        "scientific_article": "ARTYKUŁ NAUKOWY",
        "power_of_attorney": "PEŁNOMOCNICTWO",
        "contract": "UMOWA O ŚWIADCZENIE USŁUG",
        "lawsuit": "POZEW O ZAPŁATĘ",
    }
)


PEOPLE: tuple[str, ...] = (
    "Jan Kowalski",
    "Anna Nowak",
    "Piotr Zieliński",
    "Maria Wiśniewska",
    "Paweł Kamiński",
    "Ewa Lewandowska",
)


GRAMMAR_MUTATIONS: tuple[str, ...] = (
    "Niniejszy dokument zostały przygotowana do przeprowadzić badanie.",
    "Wskazane dane został wpisana przez osoby sporządzić dokument.",
    "Przekazane materiały jest przeznaczony wyłącznie do wykonywać próby.",
)


INVALID_DATES: tuple[str, ...] = (
    "31.02.2026",
    "31.04.2026",
    "29.02.2025",
)


UNSCORED_FIELD_KINDS: frozenset[str] = frozenset({"signature", "visual"})

SUPPORTED_DOCUMENT_TYPES: tuple[str, ...] = tuple(CATALOG)


def get_fields(document_type: str) -> tuple[FieldDefinition, ...]:
    try:
        return CATALOG[document_type]
    except KeyError:
        supported = ", ".join(SUPPORTED_DOCUMENT_TYPES)
        raise ValueError(
            f"Unsupported document type: {document_type!r}. Supported types: {supported}."
        ) from None


def get_title(document_type: str) -> str:
    get_fields(document_type)
    return TITLES[document_type]


def is_scored(item: FieldDefinition) -> bool:
    return not item.advisory and item.kind not in UNSCORED_FIELD_KINDS


def get_scored_fields(
    document_type: str,
) -> tuple[FieldDefinition, ...]:
    return tuple(item for item in get_fields(document_type) if is_scored(item))


def get_mutation_candidates(
    document_type: str,
    defect_kind: DefectKind,
) -> tuple[FieldDefinition, ...]:
    """Return fields eligible for a particular mutation.

    Grammar mutations affect document context, not an individual field.
    """

    if defect_kind == DefectKind.MISSING:
        return get_scored_fields(document_type)

    if defect_kind == DefectKind.INVALID_DATE:
        return tuple(item for item in get_scored_fields(document_type) if item.kind == "date")

    if defect_kind == DefectKind.GRAMMAR:
        return ()

    raise ValueError(f"Unsupported defect kind: {defect_kind!r}.")


def resolve_document_types(
    selected: Iterable[str] | None = None,
) -> tuple[str, ...]:
    """Validate selected types, preserving order and removing duplicates."""

    if selected is None:
        return SUPPORTED_DOCUMENT_TYPES

    if isinstance(selected, str):
        raise TypeError("selected must be an iterable of type names, not a string.")

    document_types = tuple(dict.fromkeys(value.strip() for value in selected))

    if not document_types:
        return SUPPORTED_DOCUMENT_TYPES

    for document_type in document_types:
        get_fields(document_type)

    return document_types
