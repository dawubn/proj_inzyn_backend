"""Regressions for Azure-only validation, including false positives from the MVP."""

import uuid
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.validation_rule import ValidationRule
from app.services.formal_context import DocumentContext
from app.services.formal_report import FormalValidationService, effective_document_type
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition
from app.services.validation import RuleEngineService, aggregate_results
from app.tasks.analysis import _upsert_report, _validate_and_save_report


def azure(text: str, confidence: float = 0.99) -> dict[str, Any]:
    lines, words, offset = [], [], 0
    for index, content in enumerate(text.splitlines()):
        polygon = [
            10,
            20 + index * 20,
            500,
            20 + index * 20,
            500,
            35 + index * 20,
            10,
            35 + index * 20,
        ]
        span = {"offset": offset, "length": len(content)}
        lines.append({"content": content, "spans": [span], "polygon": polygon})
        words.append(
            {"content": content, "span": span, "polygon": polygon, "confidence": confidence}
        )
        offset += len(content) + 1
    return {
        "content": text,
        "pages": [
            {
                "pageNumber": 1,
                "width": 600,
                "height": 800,
                "unit": "pixel",
                "lines": lines,
                "words": words,
            }
        ],
    }


def observations(text: str, confidence: float = 0.99) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        FormalDocumentExtractor()
        .extract(azure(text, confidence), DocumentType.POWER_OF_ATTORNEY)
        .fields["observations"],
    )


def rule(
    field_name: str = "has_principal_section",
    rule_type: str = "required_field",
    config: dict[str, Any] | None = None,
    active: bool = True,
) -> ValidationRule:
    return ValidationRule(
        profile_id=uuid.uuid4(),
        name="Kontrola",
        field_name=field_name,
        rule_type=rule_type,
        rule_config=config or {},
        severity=ValidationSeverity.ERROR,
        is_active=active,
        order=10,
    )


def poa_report(text: str):
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.POWER_OF_ATTORNEY).fields
    profile = get_mvp_profile_definition(DocumentType.POWER_OF_ATTORNEY)
    assert profile is not None
    return FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, profile.document_type
    )


FULL = "\n".join(
    [
        "PEŁNOMOCNICTWO",
        "Warszawa, 12.05.2026",
        "Mocodawca: Jan Kowalski",
        "Pełnomocnik: Anna Nowak",
        "Zakres pełnomocnictwa: reprezentowanie przed sądem rejonowym",
        "Podpis mocodawcy: Jan Kowalski",
    ]
)


def test_empty_form_does_not_pass_and_calendar_date_is_checked():
    issues, summary = poa_report(
        "\n".join(
            [
                "PEŁNOMOCNICTWO",
                "Data: 99.99.2026",
                "Mocodawca: ........",
                "Pełnomocnik: ........",
                "Zakres pełnomocnictwa: ........",
                "Podpis: ........",
            ]
        )
    )
    assert summary["validation_status"] == "incomplete"
    assert summary["is_complete"] is False
    assert summary["completeness_score"] is None  # Signature cannot be resolved by Read OCR.
    assert summary["counts"] == {
        "pass": 0,
        "fail": 4,
        "unknown": 1,
        "not_applicable": 0,
        "error": 0,
    }
    assert len(issues) == 5


@pytest.mark.parametrize(
    "value,state",
    [
        ("Jan Kowalski", "present"),
        ("........", "empty"),
        ("_____", "empty"),
        ("", "empty"),
        ("[UTAJNIONO: IMIE/NAZWISKO]", "masked"),
    ],
)
def test_principal_value_states(value, state):
    text = (
        f"PEŁNOMOCNICTWO\nMocodawca: {value}\nPełnomocnik: Anna Nowak\n"
        "Zakres pełnomocnictwa: reprezentowanie przed sądem"
    )
    assert observations(text)["has_principal_section"]["state"] == state


def test_principal_value_on_next_line_keeps_both_lines_as_evidence():
    result = observations(FULL.replace("Mocodawca: Jan Kowalski", "Mocodawca:\nJan Kowalski"))[
        "has_principal_section"
    ]
    assert result["state"] == "present"
    assert [item["text"] for item in result["evidence"]] == ["Mocodawca:", "Jan Kowalski"]
    assert result["evidence"][0]["page"] == 1
    assert result["evidence"][0]["polygon"]
    assert result["evidence"][0]["source"] == "azure_ocr"


def test_signature_caption_cannot_supply_principal_data():
    result = observations(FULL.replace("Mocodawca: Jan Kowalski\n", ""))
    assert result["has_principal_section"]["state"] == "missing"


@pytest.mark.parametrize(
    "caption", ["Podpis: ........", "Podpis: Jan Kowalski", "", "Podpis elektroniczny"]
)
def test_signature_always_requires_visual_verification(caption):
    result = observations(FULL + "\n" + caption)["has_signature"]
    assert result["state"] == "unknown"
    assert result["reason_code"] == "SIGNATURE_REQUIRES_VISUAL_REVIEW"


def test_page_counter_is_not_party_identification():
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure("UMOWA\nStrona 1 z 3\nPrzedmiot umowy: wykonanie usługi informatycznej"),
            DocumentType.CONTRACT,
        )
        .fields
    )
    assert fields["has_parties_section"] is False


def test_lawsuit_title_is_not_claim_content():
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure(
                "POZEW\nPowód: Jan Kowalski\nPozwany: Anna Nowak\n"
                "Uzasadnienie: opis okoliczności sprawy"
            ),
            DocumentType.LAWSUIT,
        )
        .fields
    )
    assert fields["has_claim_section"] is False


def test_citation_date_does_not_fill_empty_document_date():
    result = observations(
        FULL.replace(
            "Warszawa, 12.05.2026",
            "Data zawarcia: ........\nNa podstawie ustawy z dnia 23.04.1964 r.",
        )
    )
    assert result["has_date"]["state"] == "empty"


@pytest.mark.parametrize(
    "value,state",
    [
        ("2026-05-12", "present"),
        ("29.02.2024", "present"),
        ("29.02.2025", "invalid"),
        ("31.04.2026", "invalid"),
        ("7 czerwca 2026", "present"),
        ("12 . 05 . 2026", "present"),
        ("[UTAJNIONO: DATA]", "masked"),
    ],
)
def test_document_date(value, state):
    result = observations(FULL.replace("Warszawa, 12.05.2026", "Data sporządzenia: " + value))[
        "has_date"
    ]
    assert result["state"] == state


def test_two_document_dates_are_ambiguous():
    result = observations(FULL + "\nData sporządzenia: 12.05.2026\nData sporządzenia: 13.05.2026")[
        "has_date"
    ]
    assert result["state"] == "unknown"
    assert result["reason_code"] == "CONFLICTING_DATES"


def test_low_confidence_is_not_a_confirmed_missing_field():
    result = observations(FULL.replace("Jan Kowalski", "........"), confidence=0.4)
    assert result["has_principal_section"]["state"] == "unknown"


def test_unreadable_page_prevents_asserting_absence():
    raw = azure(FULL.replace("Mocodawca: Jan Kowalski\n", ""))
    raw["pages"].append({"pageNumber": 2, "lines": []})
    result = FormalDocumentExtractor().extract(raw, DocumentType.POWER_OF_ATTORNEY)
    assert result.fields["observations"]["has_principal_section"]["state"] == "unknown"


def test_azure_snake_case_and_line_strings_are_supported():
    context = DocumentContext.from_azure(
        {"pages": [{"page_number": 3, "lines": ["Mocodawca: Jan Kowalski"]}]}
    )
    assert context.lines[0].page == 3
    assert context.text == "Mocodawca: Jan Kowalski"


def test_local_ocr_cannot_supply_formal_facts():
    result = FormalDocumentExtractor().extract(
        {"words_per_page": [[{"tekst": FULL}]], "provider": "local_tesseract"}
    )
    assert not result.fields["has_text"]
    assert result.fields["ocr_limitations"] == ["AZURE_OCR_REQUIRED"]


def test_redacted_values_pass_presence_checks_without_claiming_content_validity():
    issues, summary = poa_report(
        FULL.replace("12.05.2026", "[UTAJNIONO: DATA]").replace(
            "Jan Kowalski", "[UTAJNIONO: IMIE/NAZWISKO]"
        )
    )
    assert summary["validation_status"] == "indeterminate"
    assert summary["is_complete"] is None
    assert not any(issue.severity == "error" for issue in issues)
    assert summary["counts"]["unknown"] == 1  # Signature only.
    for result in summary["rule_results"]:
        if result["field_name"] in {"has_date", "has_principal_section"}:
            assert result["outcome"] == "pass"
            assert result["reason_code"] == "REDACTED_PRESENCE_CONFIRMED"
            assert "nie była sprawdzana" in result["message"]


def test_no_profile_is_never_a_complete_report():
    _, summary = FormalValidationService().build_report(
        [], {"has_text": True}, None, DocumentType.INVOICE
    )
    assert summary["validation_status"] == "unsupported"
    assert summary["is_complete"] is None
    assert summary["completeness_score"] is None


def test_empty_or_inactive_profile_is_not_supported():
    for rules in ([], [rule(active=False)]):
        _, summary = FormalValidationService().build_report(
            rules, {"has_text": True}, "empty", DocumentType.CONTRACT
        )
        assert summary["validation_status"] == "unsupported"


def test_same_azure_input_and_rule_definitions_give_same_report():
    first = poa_report(FULL)
    second = poa_report(FULL)
    assert first == second  # Random database/profile IDs are not content versions.


def test_profile_definition_change_changes_version():
    service = FormalValidationService()
    rules = [rule()]
    _, first = service.build_report(rules, {"has_text": True}, "p", DocumentType.CONTRACT)
    rules[0].rule_config = {"message": "Nowy komunikat"}
    _, second = service.build_report(rules, {"has_text": True}, "p", DocumentType.CONTRACT)
    assert first["profile_version"] != second["profile_version"]


@pytest.mark.parametrize(
    "kind,config", [("unknown", {}), ("regex_match", {"pattern": "["}), ("date_range", {})]
)
def test_invalid_rule_configuration_is_visible(kind, config):
    results = RuleEngineService().evaluate([rule("value", kind, config)], {"value": "test"})
    assert results[0].outcome == "error"
    assert aggregate_results(results, True)["is_complete"] is None


def test_date_range_is_actually_evaluated():
    item = rule("date", "date_range", {"min": "2026-01-01", "max": "2026-12-31"})
    assert RuleEngineService().evaluate([item], {"date": "2025-01-01"})[0].outcome == "fail"
    assert RuleEngineService().evaluate([item], {"date": "2026-10-26"})[0].outcome == "pass"


def test_zero_is_a_valid_required_numeric_value():
    assert RuleEngineService().evaluate([rule("amount")], {"amount": 0})[0].outcome == "pass"


def test_legacy_flags_cannot_silently_pass_new_validation():
    result = RuleEngineService().evaluate([rule()], {"has_principal_section": True})[0]
    assert result.outcome == "unknown"
    assert result.reason_code == "LEGACY_EXTRACTION"


def test_user_type_takes_precedence_and_low_confidence_auto_is_unknown():
    assert (
        effective_document_type(DocumentType.POWER_OF_ATTORNEY, DocumentType.INVOICE, 0.99)
        == DocumentType.POWER_OF_ATTORNEY
    )
    assert (
        effective_document_type(DocumentType.UNKNOWN, DocumentType.CONTRACT, 0.14)
        == DocumentType.UNKNOWN
    )


def test_pipeline_selects_user_profile_and_preserves_detected_type():
    analysis = DocumentAnalysis(id=uuid.uuid4(), ocr_raw_result=azure(FULL))
    document = Document(document_type=DocumentType.POWER_OF_ATTORNEY)
    with (
        patch(
            "app.tasks.analysis._load_rules_for_document_type", return_value=(None, [])
        ) as loader,
        patch("app.tasks.analysis._upsert_report") as save,
    ):
        _validate_and_save_report(MagicMock(), analysis, document, DocumentType.INVOICE, 0.99)
    assert loader.call_args.args[1] == DocumentType.POWER_OF_ATTORNEY
    summary = save.call_args.args[-1]
    assert summary["detected_document_type"] == "invoice"
    assert summary["effective_document_type"] == "power_of_attorney"
    assert summary["type_source"] == "user"


def test_report_persistence_keeps_unknown_aggregate_nullable():
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    _upsert_report(session, uuid.uuid4(), [], 0, {"is_complete": None, "completeness_score": None})
    report = session.add.call_args.args[0]
    assert report.is_complete is None
    assert report.completeness_score is None


def test_masked_name_does_not_hide_readable_authorization_scope():
    text = FULL.replace(
        "Zakres pełnomocnictwa: reprezentowanie przed sądem rejonowym",
        "Upoważniam [UTAJNIONO: IMIE/NAZWISKO] do reprezentowania przed sądem.",
    )
    assert observations(text)["has_authorization_scope_section"]["state"] == "present"


def test_name_without_action_is_not_an_authorization_scope():
    text = FULL.replace(
        "Zakres pełnomocnictwa: reprezentowanie przed sądem rejonowym", "Upoważniam Annę Nowak."
    )
    assert observations(text)["has_authorization_scope_section"]["state"] == "unknown"


def test_no_attachment_requirement_is_not_applicable():
    assert observations(FULL)["has_attachments_section"]["state"] == "not_applicable"


def test_reference_alone_does_not_prove_attachment_presence():
    result = observations(FULL + "\nDokument opisano w załączniku nr 1.")["has_attachments_section"]
    assert result["state"] == "unknown"


def test_preview_words_use_azure_coordinates():
    from app.tasks.legal_analysis import _extract_azure_words

    result = _extract_azure_words(azure("Mocodawca: Jan Kowalski"))
    assert result[0]["source"] == "azure_ocr"
    assert result[0]["bbox"] == {"x": 10, "y": 20, "width": 490, "height": 15}


@pytest.mark.parametrize(
    "filename,expected_failures",
    [
        ("01_pelnomocnictwo_wypelnione", set()),
        ("02_puste_dane_mocodawcy", {"has_principal_section"}),
        ("03_pusty_zakres", {"has_authorization_scope_section"}),
        ("04_pusta_data_z_cytatem", {"has_date"}),
        ("05_dane_zamaskowane", set()),
    ],
)
def test_recorded_azure_responses_after_real_redaction(filename, expected_failures):
    import json
    from pathlib import Path

    raw = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "formal_v2" / f"{filename}.ocr.json").read_text()
    )
    fields = FormalDocumentExtractor().extract(raw, DocumentType.POWER_OF_ATTORNEY).fields
    profile = get_mvp_profile_definition(DocumentType.POWER_OF_ATTORNEY)
    assert profile is not None
    _, summary = FormalValidationService().build_report(
        build_rule_models(profile),
        fields,
        profile.name,
        profile.document_type,
    )
    actual = {r["field_name"] for r in summary["rule_results"] if r["outcome"] == "fail"}
    assert actual == expected_failures
    for result in summary["rule_results"]:
        if (
            result["field_name"]
            in {"has_date", "has_principal_section", "has_attorney_section"} - expected_failures
        ):
            assert result["outcome"] == "pass", result
    assert summary["validation_status"] == ("incomplete" if expected_failures else "indeterminate")


@pytest.mark.parametrize("confidence", [0.99, 0.4])
def test_recognized_mask_in_role_field_confirms_presence(confidence):
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure(FULL.replace("Jan Kowalski", "[UTAJNIONO: IMIE/NAZWISKO]"), confidence),
            DocumentType.POWER_OF_ATTORNEY,
        )
        .fields
    )
    result = RuleEngineService().evaluate([rule()], fields)[0]
    assert result.outcome == "pass"
    assert result.reason_code == "REDACTED_PRESENCE_CONFIRMED"
    assert fields["has_principal_section"] is True
    assert result.evidence


def test_mask_in_another_role_does_not_fill_empty_principal():
    _, summary = poa_report(
        FULL.replace("Mocodawca: Jan Kowalski", "Mocodawca: ........")
        .replace("Anna Nowak", "[UTAJNIONO: IMIE/NAZWISKO]")
        .replace("12.05.2026", "[UTAJNIONO: DATA]")
    )
    outcomes = {result["field_name"]: result["outcome"] for result in summary["rule_results"]}
    assert outcomes["has_principal_section"] == "fail"
    assert outcomes["has_attorney_section"] == "pass"
    assert outcomes["has_date"] == "pass"
    assert outcomes["has_signature"] == "unknown"


@pytest.mark.parametrize(
    "second_party,expected",
    [
        ("Pozwany: [UTAJNIONO: IMIE/NAZWISKO]", "pass"),
        ("Pozwany: Anna Nowak", "pass"),
        ("Pozwany: ........", "fail"),
        ("", "fail"),
    ],
)
def test_masked_party_does_not_establish_presence_of_both_parties(second_party, expected):
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure(
                "POZEW\nPowód: [UTAJNIONO: IMIE/NAZWISKO]\n"
                f"{second_party}\nUzasadnienie: opis sprawy"
            ),
            DocumentType.LAWSUIT,
        )
        .fields
    )
    result = RuleEngineService().evaluate([rule("has_parties_section")], fields)[0]
    assert result.outcome == expected


def test_person_mask_alone_does_not_establish_authorization_scope():
    _, summary = poa_report(
        FULL.replace("reprezentowanie przed sądem rejonowym", "[UTAJNIONO: IMIE/NAZWISKO]")
    )
    scope = next(
        r for r in summary["rule_results"] if r["field_name"] == "has_authorization_scope_section"
    )
    assert scope["outcome"] == "unknown"


@pytest.mark.parametrize(
    "kind,config",
    [("regex_match", {"pattern": r"\d{4}-\d{2}-\d{2}"}), ("date_range", {"min": "2026-01-01"})],
)
def test_mask_confirms_presence_but_not_date_format_or_range(kind, config):
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure(FULL.replace("12.05.2026", "[UTAJNIONO: DATA]")), DocumentType.POWER_OF_ATTORNEY
        )
        .fields
    )
    results = RuleEngineService().evaluate(
        [rule("has_date"), rule("has_date", kind, config)], fields
    )
    assert [result.outcome for result in results] == ["pass", "unknown"]
    assert results[1].reason_code == "MASKED_CONTENT_UNVERIFIABLE"


def test_worker_preserves_formal_results_and_adds_azure_viewer_locations():
    from app.tasks.legal_analysis import _extract_azure_words

    raw = azure(
        FULL.replace("Warszawa, 12.05.2026", "Data: ........").replace(
            "Jan Kowalski", "[UTAJNIONO: IMIE/NAZWISKO]"
        )
    )
    analysis = DocumentAnalysis(
        id=uuid.uuid4(), ocr_raw_result=raw, tesseract_words=_extract_azure_words(raw)
    )
    document = Document(document_type=DocumentType.POWER_OF_ATTORNEY)
    profile = get_mvp_profile_definition(DocumentType.POWER_OF_ATTORNEY)
    assert profile is not None
    with (
        patch(
            "app.tasks.analysis._load_rules_for_document_type",
            return_value=(profile.name, build_rule_models(profile)),
        ),
        patch("app.tasks.analysis._upsert_report") as save,
    ):
        _validate_and_save_report(MagicMock(), analysis, document, DocumentType.INVOICE, 0.99)

    issues = save.call_args.args[2]
    summary = save.call_args.args[4]
    date = next(issue for issue in issues if issue.field_name == "has_date")
    assert date.details["outcome"] == "fail"
    assert date.details["evidence"]
    assert date.details["location"]["kind"] == "placeholder"
    assert date.details["location"]["page"] == 1
    assert date.details["location"]["rects"][0]["y"] == 0.05
    principal = next(
        r for r in summary["rule_results"] if r["field_name"] == "has_principal_section"
    )
    assert principal["outcome"] == "pass"
    assert summary["effective_document_type"] == "power_of_attorney"
