"""Independent document examples plus destructive mutations of their fields."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.enums.document import DocumentType
from app.services.formal_report import FormalValidationService
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition
from app.tasks.analysis import _load_rules_for_document_type
from tests.services.test_formal_validation_v2 import azure

CASES = json.loads((Path(__file__).parents[1] / "fixtures/formal_catalog/cases.json").read_text())
BY_TYPE = {case["type"]: case for case in CASES}


def document(
    case: dict[str, Any], changes: dict[str, str] | None = None, omit: str | None = None
) -> str:
    changes = changes or {}
    return "\n".join(
        [
            case["title"],
            *[
                f"{label}: {changes.get(key, value)}"
                for key, label, value in case["rows"]
                if key != omit
            ],
        ]
    )


def extract(
    case: dict[str, Any],
    changes: dict[str, str] | None = None,
    omit: str | None = None,
    confidence: float = 0.99,
) -> dict[str, Any]:
    return (
        FormalDocumentExtractor()
        .extract(azure(document(case, changes, omit), confidence), DocumentType(case["type"]))
        .fields
    )


def report(case: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    dtype = DocumentType(case["type"])
    profile = get_mvp_profile_definition(dtype)
    assert profile is not None
    return FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, dtype
    )[1]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["type"])
def test_complete_examples_have_evidence_for_every_textual_check(case):
    fields = extract(case)
    result = report(case, fields)
    assert result["validation_profile_available"]
    assert result["validation_status"] == (
        "indeterminate" if case["type"] == "application" else "complete"
    ), result["rule_results"]
    expected_fields = {key for key, _, _ in case["rows"]}
    if case["type"] in {"id_card", "passport"}:
        expected_fields.add("has_document_image")
    assert {r["field_name"] for r in result["rule_results"]} == expected_fields
    for item in result["rule_results"]:
        visual = item["field_name"] in {"has_signature", "has_document_image"}
        assert item["outcome"] == ("unknown" if visual else "pass"), item
        if not visual:
            assert item["evidence"] and all(e["source"] == "azure_ocr" for e in item["evidence"])


BLANK_CASES = [
    (case, key) for case in CASES for key, _, _ in case["rows"] if key != "has_signature"
]


@pytest.mark.parametrize("case,key", BLANK_CASES, ids=[f"{c['type']}-{k}" for c, k in BLANK_CASES])
def test_explicit_blank_does_not_borrow_the_next_field(case, key):
    fields = extract(case, {key: "................"})
    assert fields["observations"][key]["state"] == "empty", fields["observations"][key]
    result = report(case, fields)
    item = next(r for r in result["rule_results"] if r["field_name"] == key)
    assert item["outcome"] == "fail"
    if item["severity"] == "error":
        assert result["validation_status"] == "incomplete"


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["type"])
def test_low_confidence_does_not_confirm_values(case):
    result = report(case, extract(case, confidence=0.3))
    assert all(r["outcome"] == "unknown" for r in result["rule_results"])
    assert result["completeness_score"] is None


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["type"])
def test_local_ocr_cannot_be_used_for_new_profiles(case):
    fields = (
        FormalDocumentExtractor()
        .extract(
            {"provider": "local_tesseract", "content": document(case)}, DocumentType(case["type"])
        )
        .fields
    )
    result = report(case, fields)
    assert result["validation_status"] == "indeterminate"
    assert not any(r["outcome"] == "pass" for r in result["rule_results"])


@pytest.mark.parametrize(
    "dtype,key,mask",
    [
        ("application", "has_applicant", "IMIE/NAZWISKO"),
        ("invoice", "has_seller", "IMIE/NAZWISKO"),
        ("invoice", "has_issue_date", "DATA"),
        ("id_card", "has_surname", "IMIE/NAZWISKO"),
        ("id_card", "has_pesel", "PESEL"),
        ("passport", "has_expiry_date", "DATA"),
        ("bank_statement", "has_account_holder", "IMIE/NAZWISKO"),
        ("bank_statement", "has_account_number", "NUMER RACHUNKU"),
        ("tax_form", "has_tax_identifier", "NIP"),
        ("scientific_article", "has_authors", "IMIE/NAZWISKO"),
    ],
)
def test_correct_mask_confirms_presence_even_with_low_confidence(dtype, key, mask):
    result = extract(BY_TYPE[dtype], {key: f"[UTAJNIONO: {mask}]"}, confidence=0.3)
    assert result["observations"][key]["state"] == "masked"


@pytest.mark.parametrize(
    "dtype,key,mask",
    [
        ("invoice", "has_buyer", "ADRES"),
        ("invoice", "has_invoice_total", "DATA"),
        ("id_card", "has_pesel", "TELEFON"),
        ("passport", "has_birth_date", "IMIE/NAZWISKO"),
        ("bank_statement", "has_account_number", "TELEFON"),
        ("scientific_article", "has_article_content", "IMIE/NAZWISKO"),
    ],
)
def test_wrong_mask_does_not_confirm_an_unrelated_value(dtype, key, mask):
    result = extract(BY_TYPE[dtype], {key: f"[UTAJNIONO: {mask}]"})
    assert result["observations"][key]["state"] == "unknown"


@pytest.mark.parametrize("dtype", list(DocumentType))
def test_every_concrete_classifier_category_has_a_profile(dtype):
    profile = get_mvp_profile_definition(dtype)
    assert bool(profile) == (dtype not in {DocumentType.UNKNOWN, DocumentType.OTHER})
    if profile:
        assert profile.rules and len({r.field_name for r in profile.rules}) == len(profile.rules)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["type"])
def test_worker_loads_builtin_profile_without_database_seed(case):
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    name, rules = _load_rules_for_document_type(session, DocumentType(case["type"]))
    assert name and rules
    assert {r.field_name for r in rules} >= {key for key, _, _ in case["rows"]}


def test_zero_balance_and_no_transactions_are_valid_content():
    fields = extract(BY_TYPE["bank_statement"])["observations"]
    assert fields["has_opening_balance"]["state"] == "present"
    assert fields["has_transactions"]["reason_code"] == "EXPLICIT_NO_TRANSACTIONS"


def test_explicit_empty_bilingual_caption_is_not_a_name():
    text = document(BY_TYPE["passport"]).replace(
        "Nazwisko: Testowski", "Nazwisko / Surname:\nImiona / Given names: Jan"
    )
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.PASSPORT).fields
    assert fields["observations"]["has_surname"]["state"] == "empty"


def test_filled_bilingual_caption_remains_one_field():
    text = document(BY_TYPE["passport"]).replace(
        "Nazwisko: Testowski", "Nazwisko / Surname: Testowski"
    )
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.PASSPORT).fields
    assert fields["observations"]["has_surname"]["state"] == "present"


def test_unlabelled_prose_mention_does_not_create_author_field():
    text = "ARTYKUŁ NAUKOWY\nW tym badaniu autorzy: Jan Testowski byli jedynie uczestnikami."
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.SCIENTIFIC_ARTICLE).fields
    assert fields["observations"]["has_authors"]["state"] == "unknown"


def test_mentions_inside_body_are_not_field_labels():
    text = (
        "ARTYKUŁ NAUKOWY\nWyniki: W badaniu autorzy: Jan Testowski uczestniczyli jako ankieterzy."
    )
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.SCIENTIFIC_ARTICLE).fields
    assert fields["observations"]["has_authors"]["state"] != "present"


def test_date_aliases_with_same_day_do_not_conflict():
    case = BY_TYPE["invoice"]
    text = document(case) + "\nIssue date: 2026-10-29"
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.INVOICE).fields
    assert fields["observations"]["has_issue_date"]["state"] == "present"


def test_tax_form_code_can_be_the_document_heading():
    case = BY_TYPE["tax_form"]
    text = document(case, omit="has_tax_form").replace(case["title"], "PIT-37")
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.TAX_FORM).fields
    assert fields["observations"]["has_tax_form"]["state"] == "present"


@pytest.mark.parametrize(
    "key,value,expected",
    [
        ("has_issue_date", "31.02.2026", "invalid"),
        ("has_invoice_total", "0,00 PLN", "present"),
        ("has_invoice_total", "-120,00 PLN", "present"),
        ("has_invoice_total", "kwota do ustalenia", "unknown"),
        ("has_invoice_number", "podaj numer faktury", "empty"),
    ],
)
def test_invoice_values_are_not_just_nonempty_strings(key, value, expected):
    result = extract(BY_TYPE["invoice"], {key: value})["observations"][key]
    assert result["state"] == expected


@pytest.mark.parametrize(
    "dtype,key",
    [
        ("scientific_article", "has_article_abstract"),
        ("patent", "has_patent_number"),
        ("financial_report", "has_financial_notes"),
        ("government_tender", "has_submission_deadline"),
        ("manual", "has_safety_instructions"),
    ],
)
def test_variant_sections_are_advisory_and_do_not_fail_completeness(dtype, key):
    case = BY_TYPE[dtype]
    summary = report(case, extract(case, omit=key))
    check = next(r for r in summary["rule_results"] if r["field_name"] == key)
    assert check["outcome"] == "unknown"
    assert check["severity"] == "warning"
    assert summary["validation_status"] == "complete"


def test_invoice_header_without_items_does_not_pass():
    case = BY_TYPE["invoice"]
    text = document(case).replace(
        "Nazwa towaru lub usługi: Przegląd instalacji grzewczej",
        "Nazwa towaru lub usługi: Ilość Cena netto",
    )
    fields = FormalDocumentExtractor().extract(azure(text), DocumentType.INVOICE).fields
    assert fields["observations"]["has_invoice_items"]["state"] == "unknown"


def test_names_in_distant_column_do_not_supply_missing_buyer():
    raw = azure("FAKTURA VAT\nNabywca:\nJan Testowski\nNumer faktury: TEST/2026")
    lines = raw["pages"][0]["lines"]
    lines[1]["polygon"] = [10, 40, 100, 40, 100, 55, 10, 55]
    lines[2]["polygon"] = [400, 40, 500, 40, 500, 55, 400, 55]
    fields = FormalDocumentExtractor().extract(raw, DocumentType.INVOICE).fields
    assert fields["observations"]["has_buyer"]["state"] == "unknown"


def test_same_row_dots_can_confirm_blank_even_when_distant():
    raw = azure("WYCIĄG BANKOWY\nNumer rachunku:\n...........\nBank: Bank Testowy")
    lines = raw["pages"][0]["lines"]
    lines[1]["polygon"] = [10, 40, 100, 40, 100, 55, 10, 55]
    lines[2]["polygon"] = [400, 40, 500, 40, 500, 55, 400, 55]
    fields = FormalDocumentExtractor().extract(raw, DocumentType.BANK_STATEMENT).fields
    assert fields["observations"]["has_account_number"]["state"] == "empty"


CLOUD_CASES = json.loads(
    (Path(__file__).parents[1] / "fixtures/formal_catalog/cloud_expected.json").read_text()
)


@pytest.mark.parametrize("case", CLOUD_CASES, ids=lambda c: c["file"])
def test_actual_azure_after_redaction(case):
    raw = json.loads(
        (
            Path(__file__).parents[1]
            / "fixtures/formal_catalog"
            / (Path(case["file"]).stem + ".ocr.json")
        ).read_text()
    )
    fields = FormalDocumentExtractor().extract(raw, DocumentType(case["type"])).fields
    result = report(case, fields)
    assert {r["field_name"]: r["outcome"] for r in result["rule_results"]} == case["expected"]
