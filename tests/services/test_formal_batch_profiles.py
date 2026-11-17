import uuid
from unittest.mock import MagicMock, patch

from app.enums.document import DocumentType
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.services.formal_report import FormalValidationService
from app.tasks.analysis import _validate_and_save_report


def test_batch_completeness_uses_each_detected_type_without_changing_classification():
    cases = [
        (DocumentType.POWER_OF_ATTORNEY, 0.5714),
        (DocumentType.POWER_OF_ATTORNEY, 0.4286),
        (DocumentType.CONTRACT, 0.35),
        (DocumentType.LAWSUIT, 0.75),
        (DocumentType.UNKNOWN, 0.2),
    ]
    with patch("app.tasks.analysis._upsert_report") as save:
        for dtype, confidence in cases:
            analysis = DocumentAnalysis(
                id=uuid.uuid4(),
                detected_document_type=dtype.value,
                classification_confidence=confidence,
                ocr_raw_result={"content": "Dokument do sprawdzenia kompletności."},
                tesseract_words=[],
            )
            document = Document(document_type=DocumentType.UNKNOWN)
            session = MagicMock()
            session.execute.return_value.scalar_one_or_none.return_value = None
            _validate_and_save_report(session, analysis, document, dtype, confidence)
            summary = save.call_args.args[-1]
            assert summary["effective_document_type"] == dtype.value
            assert summary["validation_profile_available"] == (dtype != DocumentType.UNKNOWN)
            assert summary["classification_confidence"] == confidence
            assert analysis.detected_document_type == dtype.value
            assert analysis.classification_confidence == confidence
        assert save.call_count == len(cases)


def test_uncertain_type_is_not_reported_as_unsupported():
    issues, summary = FormalValidationService().build_report(
        [], {"has_text": True}, None, DocumentType.UNKNOWN
    )
    assert summary["validation_status"] == "type_required"
    assert summary["is_complete"] is None
    assert issues[0].details["reason_code"] == "DOCUMENT_TYPE_UNCERTAIN"
