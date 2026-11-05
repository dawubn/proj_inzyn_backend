import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.validation_profile import ValidationProfile
from app.services.formal_report import FormalValidationService, coerce_document_type
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition
from app.services.report import ReportService
from app.tasks.analysis import _validate_and_save_report
from tests.services.test_formal_validation_v2 import FULL, azure


@pytest.mark.parametrize(
    ("requested", "detected", "confidence", "expected"),
    [
        ("power_of_attorney", "invoice", 0.99, DocumentType.POWER_OF_ATTORNEY),
        ("unknown", "invoice", 0.99, DocumentType.INVOICE),
        ("unknown", "invoice", 0.01, DocumentType.UNKNOWN),
        ("unknown", "obsolete_type", 0.99, DocumentType.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_service_and_worker_build_identical_reports(
    requested, detected, confidence, expected
):
    analysis = DocumentAnalysis(
        id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        ocr_raw_result=azure(FULL),
        detected_document_type=detected,
        classification_confidence=confidence,
    )
    document = Document(document_type=coerce_document_type(requested))
    definition = get_mvp_profile_definition(expected)
    name = definition.name if definition else None
    rules = build_rule_models(definition) if definition else []
    profiles = AsyncMock()
    profiles.get_active_by_document_type.return_value = None
    reports = AsyncMock()
    reports.create.side_effect = lambda report: report
    service = ReportService(
        reports,
        AsyncMock(get_by_id=AsyncMock(return_value=analysis)),
        AsyncMock(get_by_id=AsyncMock(return_value=document)),
        profiles,
    )
    report = await service.build_report(analysis.id)
    with (
        patch("app.tasks.analysis._load_rules_for_document_type", return_value=(name, rules)),
        patch("app.tasks.analysis._upsert_report") as save,
    ):
        _validate_and_save_report(
            MagicMock(), analysis, document, coerce_document_type(detected), confidence
        )
    assert report.summary == save.call_args.args[3]
    issues = save.call_args.args[2]
    assert report.errors == [
        i.model_dump() for i in issues if i.severity == ValidationSeverity.ERROR
    ]
    assert report.warnings == [
        i.model_dump() for i in issues if i.severity == ValidationSeverity.WARNING
    ]
    assert report.summary["effective_document_type"] == expected.value
    assert "document_type" not in report.summary
    profiles.get_active_by_document_type.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_service_uses_actual_database_profile_name():
    definition = get_mvp_profile_definition(DocumentType.INVOICE)
    assert definition is not None
    profile = ValidationProfile(name="Invoice custom v3", rules=build_rule_models(definition))
    analysis = DocumentAnalysis(
        id=uuid.uuid4(), document_id=uuid.uuid4(), ocr_raw_result=azure(FULL)
    )
    profiles = AsyncMock()
    profiles.get_active_by_document_type.return_value = profile
    reports = AsyncMock()
    reports.create.side_effect = lambda report: report
    report = await ReportService(
        reports,
        AsyncMock(get_by_id=AsyncMock(return_value=analysis)),
        AsyncMock(get_by_id=AsyncMock(return_value=Document(document_type=DocumentType.INVOICE))),
        profiles,
    ).build_report(analysis.id)
    assert report.summary is not None
    assert report.summary["profile_name"] == "Invoice custom v3"
    assert report.summary["type_source"] == "user"


@pytest.mark.parametrize("requested", [DocumentType.UNKNOWN, DocumentType.POWER_OF_ATTORNEY])
def test_extraction_reused_only_when_it_matches_validation_type(requested):
    raw = azure(FULL)
    extraction = FormalDocumentExtractor().extract(raw, DocumentType.POWER_OF_ATTORNEY)
    analysis = DocumentAnalysis(id=uuid.uuid4(), ocr_raw_result=raw)
    document = Document(document_type=requested)
    with (
        patch(
            "app.services.formal_validation.FormalDocumentExtractor.extract",
            wraps=FormalDocumentExtractor().extract,
        ) as extract,
        patch("app.tasks.analysis._load_rules_for_document_type", return_value=(None, [])),
        patch("app.tasks.analysis._upsert_report"),
    ):
        _validate_and_save_report(
            MagicMock(), analysis, document, DocumentType.INVOICE, 0.99, extraction
        )
    if requested == DocumentType.POWER_OF_ATTORNEY:
        extract.assert_not_called()
        assert analysis.extracted_fields is extraction.fields
    else:
        extract.assert_called_once_with(raw, DocumentType.INVOICE)
        assert analysis.extracted_fields is not extraction.fields


def test_no_text_is_review_with_unknown_completeness():
    definition = get_mvp_profile_definition(DocumentType.INVOICE)
    assert definition is not None
    fields = FormalDocumentExtractor().extract(None, DocumentType.INVOICE).fields
    issues, summary = FormalValidationService().build_report(
        build_rule_models(definition),
        fields,
        definition.name,
        DocumentType.INVOICE,
    )
    no_text = next(i for i in issues if i.details["reason_code"] == "NO_TEXT")
    assert no_text.severity == ValidationSeverity.WARNING
    assert no_text.details["outcome"] == "unknown"
    assert summary["is_complete"] is None
    assert summary["completeness_score"] is None
    assert summary["validation_status"] == "indeterminate"
