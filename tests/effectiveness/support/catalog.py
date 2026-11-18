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
            field("has_candidate", "Imię i nazwisko", "identity"),
            field("has_contact", "Kontakt", "text"),
            field("has_experience", "Doświadczenie zawodowe", "section"),
            field("has_education", "Wykształcenie", "section"),
            field("has_skills", "Umiejętności", "section", advisory=True),
            field("has_data_consent", "Klauzula", "section", advisory=True),
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
        "demand_letter": (
            field("has_creditor", "Wierzyciel", "identity"),
            field("has_debtor", "Dłużnik", "identity"),
            field("has_demand_amount", "Kwota do zapłaty", "amount"),
            field("has_payment_deadline", "Termin zapłaty", "text"),
            field("has_demand_date", "Data wezwania", "date"),
            field("has_signature", "Podpis wierzyciela", "signature"),
            field("has_demand_basis", "Podstawa roszczenia", "text", advisory=True),
        ),
    }
)


TITLES: Mapping[str, str] = MappingProxyType(
    {
        "application": "CURRICULUM VITAE",
        "invoice": "FAKTURA VAT",
        "patent": "OPIS WYNALAZKU — DOKUMENT TESTOWY",
        "power_of_attorney": "PEŁNOMOCNICTWO",
        "contract": "UMOWA O ŚWIADCZENIE USŁUG",
        "lawsuit": "POZEW O ZAPŁATĘ",
        "demand_letter": "PRZEDSĄDOWE WEZWANIE DO ZAPŁATY",
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
