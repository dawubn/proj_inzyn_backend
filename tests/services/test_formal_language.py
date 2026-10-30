"""Adversarial minimal pairs: explicit absence, negation scope and typed masks."""

import json
from pathlib import Path

import pytest

from app.enums.document import DocumentType
from app.services.formal_report import FormalValidationService
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition
from tests.services.test_formal_validation_v2 import FULL, azure, observations


@pytest.mark.parametrize(
    "value",
    [
        "nie podano danych mocodawcy",
        "nie wskazano osoby",
        "brak danych mocodawcy",
        "dane zostaną uzupełnione później",
        "nie dotyczy",
        "[imię i nazwisko]",
        "wpisz imię i nazwisko",
        "do uzupełnienia przez mocodawcę",
        "brak danych; kontakt: [UTAJNIONO: EMAIL]",
    ],
)
def test_instructions_and_absence_are_not_identity(value):
    result = observations(FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca: {value}"))
    assert result["has_principal_section"]["state"] == "empty"


@pytest.mark.parametrize("value", ["Jan Kowalski", "[Jan Kowalski]", "[UTAJNIONO: IMIE/NAZWISKO]"])
def test_real_or_redacted_identity_still_passes(value):
    result = observations(FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca: {value}"))
    assert result["has_principal_section"]["state"] in {"present", "masked"}


@pytest.mark.parametrize("category", ["DATA", "EMAIL", "TELEFON", "ADRES", "PESEL"])
def test_mask_of_unrelated_value_is_not_a_person_name(category):
    result = observations(
        FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca: [UTAJNIONO: {category}]")
    )
    assert result["has_principal_section"]["state"] == "unknown"


@pytest.mark.parametrize(
    "value",
    [
        "nie obejmuje reprezentowania przed sądem",
        "pełnomocnik nie może podpisywać umów",
        "bez prawa odbioru korespondencji",
        "zakaz podpisywania dokumentów i odbioru korespondencji",
    ],
)
def test_prohibition_alone_does_not_establish_positive_authorization(value):
    result = observations(FULL.replace("reprezentowanie przed sądem rejonowym", value))
    assert result["has_authorization_scope_section"]["state"] == "unknown"


@pytest.mark.parametrize(
    "value",
    [
        "reprezentowanie przed sądem bez prawa zawierania ugód",
        "nie tylko reprezentowanie przed sądem, ale również odbiór korespondencji",
        "nie może podpisywać umów; może odbierać korespondencję",
        "nie obejmuje podpisywania umów, ale obejmuje reprezentowanie przed sądem",
    ],
)
def test_positive_scope_with_restriction_still_passes(value):
    result = observations(FULL.replace("reprezentowanie przed sądem rejonowym", value))
    assert result["has_authorization_scope_section"]["state"] == "present"


def test_absence_of_payment_is_substantive_justification():
    text = "POZEW\nUzasadnienie: Brak zapłaty za wykonaną usługę spowodował powstanie należności."
    assert observations(text)["has_justification_section"]["state"] == "present"


def test_mask_cannot_hide_low_confidence_of_readable_authorization():
    text = FULL.replace(
        "reprezentowanie przed sądem rejonowym",
        "[UTAJNIONO: IMIE/NAZWISKO] do reprezentowania przed sądem",
    )
    result = observations(text, confidence=0.4)
    assert result["has_authorization_scope_section"]["state"] == "unknown"


def test_uncertain_absence_sentence_is_not_confirmed_missing_identity():
    raw = azure("Mocodawca:\nnie podano danych\nPełnomocnik: Anna Nowak")
    raw["pages"][0]["words"][1]["confidence"] = 0.4
    result = FormalDocumentExtractor().extract(raw).fields["observations"]
    assert result["has_principal_section"]["state"] == "unknown"


@pytest.mark.parametrize(
    "name,expected",
    [
        (
            "06_instrukcje_zamiast_danych",
            {"has_principal_section": "fail", "has_authorization_scope_section": "fail"},
        ),
        ("07_sam_zakaz", {"has_authorization_scope_section": "unknown"}),
        ("08_zakres_z_ograniczeniem", {}),
    ],
)
def test_recorded_language_documents_after_real_redaction(name, expected):
    raw = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "formal_language" / (name + ".ocr.json")
        ).read_text()
    )
    dtype = DocumentType.POWER_OF_ATTORNEY
    fields = FormalDocumentExtractor().extract(raw, dtype).fields
    profile = get_mvp_profile_definition(dtype)
    assert profile is not None
    _, summary = FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, dtype
    )
    expected = {**expected, "has_signature": "unknown"}
    for result in summary["rule_results"]:
        assert result["outcome"] == expected.get(result["field_name"], "pass"), result


@pytest.mark.parametrize(
    "name", ["06_instrukcje_zamiast_danych", "07_sam_zakaz", "08_zakres_z_ograniczeniem"]
)
def test_azure_line_order_and_coordinate_scale_do_not_change_language_results(name):
    raw = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "formal_language" / (name + ".ocr.json")
        ).read_text()
    )
    extractor = FormalDocumentExtractor()
    before = extractor.extract(raw).fields["observations"]
    for page in raw["pages"]:
        page["lines"].reverse()
        page["width"] /= 2
        page["height"] /= 2
        for item in [*page["lines"], *page["words"]]:
            item["polygon"] = [number / 2 for number in item["polygon"]]
    after = extractor.extract(raw).fields["observations"]
    assert {k: v["state"] for k, v in before.items()} == {k: v["state"] for k, v in after.items()}


def test_redacted_name_confirms_identity_even_when_contact_details_are_missing():
    value = "brak danych kontaktowych; [UTAJNIONO: IMIE/NAZWISKO]"
    result = observations(FULL.replace("Mocodawca: Jan Kowalski", f"Mocodawca: {value}"))
    assert result["has_principal_section"]["state"] == "masked"


@pytest.mark.parametrize(
    "relation",
    [
        "pierwszą stroną a drugą stroną",
        "[UTAJNIONO: IMIE/NAZWISKO] a drugą stroną",
    ],
)
def test_prose_relation_cannot_establish_identity_of_both_parties(relation):
    raw = azure(f"UMOWA\nUmowa została zawarta pomiędzy {relation}.")
    result = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields["observations"]
    assert result["has_parties_section"]["state"] not in {"present", "masked"}
