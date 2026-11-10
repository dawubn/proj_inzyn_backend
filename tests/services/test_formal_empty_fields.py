"""Generic form suggestions must remain advisory and respect field boundaries."""

from typing import Any

import pytest

from app.enums.document import DocumentType
from app.services.formal_context import DocumentContext
from app.services.formal_empty_fields import find_possible_empty_fields
from app.services.formal_issue_locator import locate_formal_issues
from app.services.formal_report import FormalValidationService
from app.services.formal_validation import FormalDocumentExtractor
from tests.services.test_formal_validation_v2 import azure, rule


def suggestions(text: str) -> list[dict[str, Any]]:
    return find_possible_empty_fields(DocumentContext.from_azure(azure(text)))


@pytest.mark.parametrize(
    "text,label",
    [
        ("przy ulicy ........", "Ulica"),
        ("przy ulicy", "Ulica"),
        ("z siedzibą w .......", "Siedziba"),
        ("z siedzibą w", "Siedziba"),
        ("PESEL)1", "PESEL"),
        ("Sygnatura sprawy: ______", "Sygnatura sprawy"),
        ("Nr działki:\n................", "Nr działki"),
        ("Oddział: …", "Oddział"),
    ],
)
def test_blank_slots_and_omitted_dots(text, label):
    result = suggestions(text)
    assert [item["label"] for item in result] == [label]
    assert result[0]["evidence"]
    assert result[0]["page"] == 1


@pytest.mark.parametrize(
    "text",
    [
        "przy ulicy ... Kwiatowej ...",
        "z siedzibą w Warszawie",
        "Ulica: Kwiatowa 12",
        "Ulica:\nKwiatowa 12",
        "Ulica: [UTAJNIONO: ADRES]",
        "Ulica:\n[UTAJNIONO: ADRES]",
        "Ulica: .....\n[UTAJNIONO: ADRES]",
        "PESEL: [UTAJNIONO: PESEL]",
        "Ulica: nie dotyczy",
        "...........................................",
        "Rozdział pierwszy ....................... 12",
        "W tej sprawie ... nie podjęto decyzji.",
        "Ulica jest częścią adresu.",
        "nazwa miejscowości oraz kod pocztowy",
    ],
)
def test_filled_values_masks_and_prose_do_not_trigger(text):
    assert suggestions(text) == []


def test_other_fields_on_same_line_do_not_hide_a_blank():
    result = suggestions("przy ulicy .... (nr domu/ nr lokalu) [UTAJNIONO: ADRES]")
    assert [item["label"] for item in result] == ["Ulica"]
    result = suggestions("przy ulicy (nr domu/ nr lokalu) 12")
    assert [item["label"] for item in result] == ["Ulica"]
    result = suggestions("przy ulicy Kwiatowej (nr domu/ nr lokalu)")
    assert [item["label"] for item in result] == ["Numer domu / lokalu"]


def test_distant_value_on_same_row_is_not_missed():
    raw = azure("Ulica:\n[UTAJNIONO: ADRES]")
    raw["pages"][0]["lines"][0]["polygon"] = [10, 20, 80, 20, 80, 35, 10, 35]
    raw["pages"][0]["lines"][1]["polygon"] = [350, 20, 500, 20, 500, 35, 350, 35]
    assert find_possible_empty_fields(DocumentContext.from_azure(raw)) == []


def test_unknown_field_in_adjacent_column_is_a_boundary():
    result = suggestions("Ulica: ...... Numer sprawy: 123/2026")
    assert [item["label"] for item in result] == ["Ulica"]


def test_same_label_on_different_pages_produces_distinct_findings():
    raw = azure("Ulica: ......")
    raw["pages"].append({**raw["pages"][0], "pageNumber": 2})
    result = find_possible_empty_fields(DocumentContext.from_azure(raw))
    assert [item["page"] for item in result] == [1, 2]
    assert len({item["id"] for item in result}) == 2


def test_low_confidence_and_missing_layout_are_not_asserted_as_blanks():
    raw = azure("Ulica: .....", confidence=0.4)
    assert find_possible_empty_fields(DocumentContext.from_azure(raw)) == []
    assert find_possible_empty_fields(DocumentContext.from_azure({"content": "Ulica: ....."})) == []


def test_warning_does_not_change_completeness_and_has_clickable_evidence():
    fields = (
        FormalDocumentExtractor().extract(azure("Mocodawca: Jan Kowalski\nUlica: .....")).fields
    )
    issues, summary = FormalValidationService().build_report(
        [rule()], fields, "Test", DocumentType.POWER_OF_ATTORNEY
    )
    assert summary["is_complete"] is True
    assert summary["completeness_score"] == 1
    assert summary["coverage"] == 1
    assert summary["additional_review_count"] == 1
    assert summary["counts"]["unknown"] == 0  # Profile checks only.
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].details["outcome"] == "unknown"
    located = locate_formal_issues(issues, [])
    assert located[0].details["location"]["page"] == 1
    assert located[0].details["location"]["rects"]


def test_profile_check_is_not_duplicated_by_generic_warning():
    fields = FormalDocumentExtractor().extract(azure("Mocodawca: ......")).fields
    issues, summary = FormalValidationService().build_report(
        [rule()], fields, "Test", DocumentType.POWER_OF_ATTORNEY
    )
    assert summary["is_complete"] is False
    assert summary["additional_review_count"] == 0
    assert len(issues) == 1


def test_suggestions_also_work_without_a_document_profile():
    fields = FormalDocumentExtractor().extract(azure("Ulica: .....")).fields
    issues, summary = FormalValidationService().build_report([], fields, None, DocumentType.UNKNOWN)
    assert summary["validation_status"] == "unsupported"
    assert summary["additional_review_count"] == 1
    assert any(issue.details.get("advisory") for issue in issues)


@pytest.mark.parametrize("value", ["[UTAJNIONO: PESEL]", "12345678901"])
def test_identifier_caption_below_a_filled_value_is_not_empty(value):
    assert suggestions(f"{value}\nPESEL/NIP") == []


def test_generic_field_with_mask_on_next_line_is_not_empty():
    assert suggestions("Adres dodatkowy: ....\n[UTAJNIONO: ADRES]") == []


def test_populated_table_headers_do_not_generate_empty_field_warnings():
    assert suggestions("Ulica:       Kod pocztowy:\nKwiatowa 12   00-001") == []


def test_profile_result_for_same_row_does_not_hide_a_different_blank_field():
    fields = (
        FormalDocumentExtractor()
        .extract(azure("Mocodawca: Jan Kowalski, przy ulicy ........"))
        .fields
    )
    issues, summary = FormalValidationService().build_report(
        [rule()], fields, "Test", DocumentType.POWER_OF_ATTORNEY
    )
    assert summary["additional_review_count"] == 1
    assert issues[0].rule_name == "Ulica"


def test_identifier_caption_in_real_redacted_form_is_not_flagged():
    import json
    from pathlib import Path

    path = (
        Path(__file__).parents[1] / "fixtures/formal_complex/02_pelnomocnictwo_formularz.ocr.json"
    )
    fields = FormalDocumentExtractor().extract(json.loads(path.read_text())).fields
    assert not any("PESEL" in item["label"] for item in fields["possible_empty_fields"])
