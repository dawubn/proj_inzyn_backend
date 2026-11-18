"""Real Azure recordings and adversarial variations of the complex PDF corpus."""

import json
from pathlib import Path

import pytest

from app.enums.document import DocumentType
from app.services.formal_context import DocumentContext, reading_lines
from app.services.formal_report import FormalValidationService
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition
from tests.services.test_formal_validation_v2 import FULL, azure, observations

CASES = [
    ("01_pelnomocnictwo_programowe", "power_of_attorney", 3, "has_date"),
    ("02_pelnomocnictwo_formularz", "power_of_attorney", 1, "has_attorney_section"),
    ("03_pelnomocnictwo_opisowe", "power_of_attorney", 2, "has_authorization_scope_section"),
    ("04_umowa_uslugowa", "contract", 3, "has_date"),
    ("05_pozew_o_zaplate", "lawsuit", 2, "has_claim_section"),
]


@pytest.mark.parametrize("filename,kind,pages,missing", CASES)
def test_real_complex_documents(filename, kind, pages, missing):
    raw = json.loads(
        (
            Path(__file__).parents[1] / "fixtures" / "formal_complex" / f"{filename}.ocr.json"
        ).read_text()
    )
    assert len(raw["pages"]) == pages
    assert [page["pageNumber"] for page in raw["pages"]] == list(range(1, pages + 1))
    dtype = DocumentType(kind)
    fields = FormalDocumentExtractor().extract(raw, dtype).fields
    profile = get_mvp_profile_definition(dtype)
    assert profile is not None
    _, summary = FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, dtype
    )
    for result in summary["rule_results"]:
        if result["field_name"] == missing:
            expected = "fail"
        elif result["field_name"] == "has_signature":
            expected = "unknown"
        else:
            expected = "pass"
        assert result["outcome"] == expected, result
        assert result["evidence"], result
    assert summary["validation_status"] == "incomplete"


def test_evidence_motion_is_not_lawsuit_claim():
    text = "POZEW\nŻądanie pozwu:\n........\nUzasadnienie: opis okoliczności sprawy\n"
    text += "Wnoszę o przeprowadzenie dowodu z zeznań świadka."
    assert observations(text)["has_claim_section"]["state"] == "empty"


def test_transaction_date_does_not_override_document_date():
    text = FULL + "\nData przekazania wyników: 20.10.2026"
    assert observations(text)["has_date"]["value"] == "2026-05-12"


def test_party_obligations_do_not_supply_identity():
    raw = azure("UMOWA\nWykonawca przygotuje dokumentację.\nZamawiający odbierze wyniki.")
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert not fields["has_parties_section"]


@pytest.mark.parametrize(
    "kind,left,right",
    [
        (DocumentType.LAWSUIT, "Powód", "Pozwana"),
        (DocumentType.LAWSUIT, "Powódka", "Pozwany"),
        (DocumentType.LAWSUIT, "Strona powodowa", "Strona pozwana"),
        (DocumentType.CONTRACT, "Zamawiający", "Wykonawca"),
        (DocumentType.CONTRACT, "Zleceniodawca", "Zleceniobiorca"),
        (DocumentType.CONTRACT, "Kupujący", "Sprzedający"),
        (DocumentType.CONTRACT, "Nabywca", "Sprzedawca"),
    ],
)
@pytest.mark.parametrize("separator", [":", ";"])
def test_person_masks_identify_parties_when_ocr_loses_the_colon(kind, left, right, separator):
    mask = f"[UTAJNIONO{separator} IMIE/NAZWISKO]"
    raw = azure(f"Dokument zawiera dane obu stron.\n{left} {mask}\n{right}: {mask}")
    fields = FormalDocumentExtractor().extract(raw, kind).fields
    assert fields["observations"]["has_parties_section"]["state"] == "masked"


@pytest.mark.parametrize(
    "claimant",
    [
        "Powód wnosi o zapłatę należności.",
        "Powód działając przez [UTAJNIONO: IMIE/NAZWISKO] wnosi o zapłatę.",
        "Powód [UTAJNIONO: ADRES]",
        "Powód [UTAJNIONO: EMAIL]",
        "Powód ........",
        "Powództwo [UTAJNIONO: IMIE/NAZWISKO]",
    ],
)
def test_missing_colon_does_not_turn_prose_or_other_masks_into_a_person(claimant):
    raw = azure(f"POZEW O ZAPŁATĘ\n{claimant}\nPozwana: [UTAJNIONO: IMIE/NAZWISKO]")
    fields = FormalDocumentExtractor().extract(raw, DocumentType.LAWSUIT).fields
    assert not fields["has_parties_section"]


def test_small_mask_on_same_row_joins_role_even_if_azure_emits_it_last():
    raw = azure("Mocodawca:\nPełnomocnik: Anna Nowak\n[UTAJNIONO: IMIE/NAZWISKO]")
    raw["pages"][0]["lines"][0]["polygon"] = [10, 20, 150, 20, 150, 35, 10, 35]
    raw["pages"][0]["lines"][2]["polygon"] = [155, 14, 250, 14, 250, 24, 155, 24]
    result = FormalDocumentExtractor().extract(raw).fields["observations"]["has_principal_section"]
    assert result["state"] == "masked"
    assert [e["text"] for e in result["evidence"]] == ["Mocodawca:", "[UTAJNIONO: IMIE/NAZWISKO]"]


def test_distant_column_does_not_fill_empty_role():
    raw = azure("Mocodawca:\n[UTAJNIONO: IMIE/NAZWISKO]\nPełnomocnik: Anna Nowak")
    raw["pages"][0]["lines"][0]["polygon"] = [10, 20, 100, 20, 100, 35, 10, 35]
    raw["pages"][0]["lines"][1]["polygon"] = [400, 20, 500, 20, 500, 35, 400, 35]
    context = DocumentContext.from_azure(raw)
    assert len(reading_lines(context.lines)) == 3
    result = FormalDocumentExtractor().extract(raw).fields["observations"]["has_principal_section"]
    assert result["state"] == "empty"


def test_signature_boxes_do_not_conflict_with_party_identities():
    raw = azure(
        "UMOWA\nZamawiający: Jan Kowalski\nWykonawca: Anna Nowak\n"
        "Zamawiający\nPodpis: .........\nWykonawca\nPodpis: ........."
    )
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["has_parties_section"]


@pytest.mark.parametrize("end,expected", [("01.01.2026", "fail"), ("31.12.2026", "pass")])
def test_real_masked_contract_compares_readable_dates_and_confirms_parties(end, expected):
    recording = (
        Path(__file__).parents[1] / "fixtures/formal_complex/masked_contract_dates.ocr.json"
    ).read_text()
    raw = json.loads(recording.replace("01.01.2026", end))
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["ocr_limitations"] == ["LOW_OCR_CONFIDENCE"]
    assert fields["observations"]["period_start"]["state"] == "present"
    assert fields["observations"]["period_end"]["state"] == "present"
    assert fields["observations"]["has_parties_section"]["state"] == "masked"
    profile = get_mvp_profile_definition(DocumentType.CONTRACT)
    assert profile is not None
    _, summary = FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, DocumentType.CONTRACT
    )
    results = {r["field_name"]: r for r in summary["rule_results"]}
    assert results["period_end"]["outcome"] == expected
    assert results["has_parties_section"]["outcome"] == "pass"
    assert results["has_parties_section"]["reason_code"] == "REDACTED_PRESENCE_CONFIRMED"
    assert results["has_attachments_section"]["outcome"] == "not_applicable"
    assert results["has_attachments_section"]["reason_code"] == "NO_ATTACHMENT_REQUIREMENT"


def test_uncertain_document_text_keeps_attachment_review():
    raw = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/formal_complex/masked_contract_dates.ocr.json"
        ).read_text()
    )
    word = next(w for w in raw["pages"][0]["words"] if w["content"] == "Dokument")
    word["confidence"] = 0.4
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["observations"]["has_attachments_section"]["state"] == "unknown"


def test_unreadable_page_keeps_attachment_review_despite_readable_masks():
    raw = json.loads(
        (
            Path(__file__).parents[1] / "fixtures/formal_complex/masked_contract_dates.ocr.json"
        ).read_text()
    )
    raw["pages"].append({"pageNumber": 2, "lines": [], "words": []})
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["observations"]["has_attachments_section"]["state"] == "unknown"


@pytest.mark.parametrize("heading", ["5. Podpisy stron", "Podpisy stron:", "Podpisy"])
@pytest.mark.parametrize("party", ["Jan Kowalski", "[UTAJNIONO: IMIE/NAZWISKO]"])
def test_party_labels_in_signature_section_do_not_override_identity(heading, party):
    raw = azure(
        f"UMOWA\nZamawiający: {party}\nWykonawca: {party}\n"
        f"{heading}\nZamawiający: ........\nWykonawca: ........"
    )
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["observations"]["has_parties_section"]["state"] in {"present", "masked"}


def test_signature_names_alone_do_not_supply_party_identities():
    raw = azure("UMOWA\n5. Podpisy stron\nZamawiający: Jan Kowalski\nWykonawca: Anna Nowak")
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert not fields["has_parties_section"]


def test_new_section_after_signatures_checks_identity_fields_again():
    raw = azure(
        "UMOWA\nZamawiający: Jan Kowalski\nWykonawca: Anna Nowak\n"
        "5. Podpisy stron\nZamawiający: ........\nWykonawca: ........\n"
        "6. Dane stron do aneksu\nWykonawca: ........"
    )
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["observations"]["has_parties_section"]["state"] == "unknown"


def test_empty_identity_outside_signature_section_remains_a_conflict():
    raw = azure(
        "UMOWA\nZamawiający: [UTAJNIONO: IMIE/NAZWISKO]\n"
        "Wykonawca: [UTAJNIONO: IMIE/NAZWISKO]\nWykonawca: ........"
    )
    fields = FormalDocumentExtractor().extract(raw, DocumentType.CONTRACT).fields
    assert fields["observations"]["has_parties_section"]["state"] == "unknown"


def test_low_confidence_real_value_still_needs_review():
    assert observations(FULL, confidence=0.4)["has_principal_section"]["state"] == "unknown"


def test_scope_action_is_not_a_signature_caption():
    text = FULL.replace(
        "Zakres pełnomocnictwa: reprezentowanie przed sądem rejonowym",
        "do:\n1. Podpisywania umów w imieniu mocodawcy",
    )
    assert observations(text)["has_authorization_scope_section"]["state"] == "present"


def test_authorized_action_cannot_supply_attorney_identity():
    text = (
        "PEŁNOMOCNICTWO\nJa, Jan Kowalski\nUdzielam pełnomocnictwa do reprezentowania przed sądem."
    )
    result = observations(text)
    assert result["has_attorney_section"]["state"] == "missing"
    assert result["has_authorization_scope_section"]["state"] == "present"
