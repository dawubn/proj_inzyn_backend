"""Role boundaries and line wrapping must not transfer facts to another field."""

import pytest

from app.services.formal_validation import FormalDocumentExtractor
from tests.services.test_formal_validation_v2 import FULL, observations


@pytest.mark.parametrize(
    "other_role",
    [
        "Świadek",
        "Osoba kontaktowa",
        "Sporządził",
        "Autor",
        "Notariusz",
        "Tłumacz",
    ],
)
@pytest.mark.parametrize("name", ["Jan Kowalski", "[UTAJNIONO: IMIE/NAZWISKO]"])
def test_other_person_does_not_fill_empty_principal(other_role, name):
    text = FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca:\n{other_role}: {name}")
    assert observations(text)["has_principal_section"]["state"] == "empty"


@pytest.mark.parametrize(
    "value",
    ["zgodnie z treścią dokumentu", "jak wyżej", "patrz załącznik nr 1", "dane w załączniku"],
)
def test_reference_is_not_readable_identity(value):
    text = FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca: {value}")
    assert observations(text)["has_principal_section"]["state"] == "unknown"


def test_two_inline_roles_do_not_share_masked_identity():
    text = FULL.replace(
        "Mocodawca: Jan Kowalski\nPełnomocnik: Anna Nowak",
        "Mocodawca: ...... Pełnomocnik: [UTAJNIONO: IMIE/NAZWISKO]",
    )
    result = observations(text)
    assert result["has_principal_section"]["state"] == "empty"
    assert result["has_attorney_section"]["state"] == "masked"


@pytest.mark.parametrize(
    "role", ["Świadek", "Autor", "Notariusz", "Tłumacz", "Sporządził", "Osoba kontaktowa"]
)
def test_other_role_on_same_line_does_not_fill_principal(role):
    text = FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca: {role}: [UTAJNIONO: IMIE/NAZWISKO]")
    assert observations(text)["has_principal_section"]["state"] == "empty"


def test_inline_field_offsets_survive_non_polish_names():
    text = FULL.replace(
        "Mocodawca: Jan Kowalski\nPełnomocnik: Anna Nowak",
        "MOCODAWCA: Jan Weiß PEŁNOMOCNIK: Anna Nowak",
    )
    result = observations(text)
    assert result["has_principal_section"]["value"] == "Jan Weiß"
    assert result["has_attorney_section"]["value"] == "Anna Nowak"


def test_several_fields_in_one_azure_row_keep_separate_values_and_original_evidence():
    result = observations(FULL.replace("\n", " "))
    for field in [
        "has_date",
        "has_principal_section",
        "has_attorney_section",
        "has_authorization_scope_section",
    ]:
        assert result[field]["state"] == "present", result[field]
        assert result[field]["evidence"]


@pytest.mark.parametrize(
    "prefix,expected",
    [
        ("pełnomocnik jest upoważniony", "present"),
        ("pełnomocnik nie jest upoważniony", "unknown"),
        ("", "present"),
        ("........", "empty"),
    ],
)
def test_wrapped_scope_keeps_negation_and_explicit_blanks(prefix, expected):
    text = FULL.replace(
        "Zakres pełnomocnictwa: reprezentowanie przed sądem rejonowym",
        f"Zakres pełnomocnictwa: {prefix}\ndo reprezentowania przed sądem rejonowym",
    )
    assert observations(text)["has_authorization_scope_section"]["state"] == expected


def test_explicit_scope_is_not_filled_by_later_unrelated_action():
    text = FULL.replace("reprezentowanie przed sądem rejonowym", "........")
    text += (
        "\nInstrukcja wypełnienia dokumentu\ndo reprezentowania należy wskazać odpowiednią osobę."
    )
    assert observations(text)["has_authorization_scope_section"]["state"] == "empty"


@pytest.mark.parametrize(
    "unrelated_date",
    [
        "Mocodawca: Jan Kowalski, ur. 12.05.1980",
        "Data urodzenia: 12.05.1980",
        "Data wpływu: 12.05.2026",
        "Pełnomocnik: Anna Nowak, numer uprawnień 12/05/2026",
    ],
)
def test_unrelated_header_date_is_not_document_date(unrelated_date):
    text = FULL.replace("Warszawa, 12.05.2026", unrelated_date)
    assert observations(text)["has_date"]["state"] == "missing"


def test_conflicting_date_row_is_not_overridden_by_another_valid_row():
    text = FULL.replace("Warszawa, 12.05.2026", "Data: 12.05.2026 lub 13.05.2026\nData: 12.05.2026")
    result = observations(text)["has_date"]
    assert result["state"] == "unknown"
    assert result["reason_code"] == "CONFLICTING_DATES"


@pytest.mark.parametrize(
    "scope,expected",
    [
        ("podpisywanie umów jest zabronione", "unknown"),
        ("reprezentowanie przed sądem nie jest dozwolone", "unknown"),
        ("podpisywanie umów nie wymaga osobnej zgody", "present"),
        ("reprezentowanie przed sądem; podpisywanie umów jest zabronione", "present"),
    ],
)
def test_scope_with_postposed_prohibition(scope, expected):
    text = FULL.replace("reprezentowanie przed sądem rejonowym", scope)
    assert observations(text)["has_authorization_scope_section"]["state"] == expected


@pytest.mark.parametrize("heading,expected", [("", "present"), (".......", "empty")])
@pytest.mark.parametrize("with_layout", [True, False])
def test_scope_heading_can_be_followed_by_separate_authorization(heading, expected, with_layout):
    text = FULL.replace(
        "Zakres pełnomocnictwa: reprezentowanie przed sądem rejonowym",
        f"Zakres pełnomocnictwa: {heading}\nUpoważniam do reprezentowania przed sądem.",
    )
    result = (
        observations(text)
        if with_layout
        else FormalDocumentExtractor().extract({"content": text}).fields["observations"]
    )
    assert result["has_authorization_scope_section"]["state"] == expected
