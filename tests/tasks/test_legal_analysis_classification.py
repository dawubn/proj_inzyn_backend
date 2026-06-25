"""
Tests verifying that legal_analysis task includes TF-IDF + LR classification step.

All imports inside run_legal_analysis_task are done locally, so patches target
the source modules (e.g. app.services.classification.ClassificationService),
not the task module.
"""

from __future__ import annotations

import contextlib
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from app.enums.document import DocumentType

ANALYSIS_UUID = "00000000-0000-0000-0000-000000000001"
DOCUMENT_UUID = "00000000-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _make_extraction(
    document_type: DocumentType = DocumentType.UNKNOWN,
    text: str = "",
    confidence: float = 0.5,
) -> Any:
    fields = {"has_text": bool(text), "document_text": text}
    return SimpleNamespace(document_type=document_type, confidence=confidence, fields=fields)


def _make_ocr_result(text: str = "invoice content netto VAT brutto") -> Any:
    return SimpleNamespace(raw={"content": text}, text_content=text, page_count=1, provider="azure")


def _make_temp_pdf() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF-1.4 test")
        return Path(tmp.name)


def _build_db_objects(session: MagicMock, tmp_path: Path) -> tuple[MagicMock, MagicMock]:
    """Configure mock session.get to return fake DocumentAnalysis + Document."""
    from app.enums.analysis import AnalysisStatus, ProcessingStage

    fake_doc = MagicMock()
    fake_doc.storage_path = str(tmp_path)
    fake_doc.original_filename = "test.pdf"
    fake_doc.mime_type = "application/pdf"
    fake_doc.document_type = DocumentType.UNKNOWN

    fake_analysis = MagicMock()
    fake_analysis.id = ANALYSIS_UUID
    fake_analysis.status = AnalysisStatus.PENDING
    fake_analysis.processing_stage = ProcessingStage.PENDING
    fake_analysis.processing_step = 0
    fake_analysis.ocr_raw_result = None
    fake_analysis.tesseract_words = []
    fake_analysis.legal_analysis_result = None

    def _get(model_class: Any, pk: Any) -> Any:
        name = getattr(model_class, "__name__", str(model_class))
        if "DocumentAnalysis" in name:
            return fake_analysis
        if "Document" in name:
            return fake_doc
        return None

    session.get.side_effect = _get
    return fake_doc, fake_analysis


def _run_task_with_patches(
    ocr_result: Any,
    extractor_inst: MagicMock,
    classifier_cls: MagicMock,
    session: MagicMock,
) -> dict[str, Any]:
    """Apply all required patches and call run_legal_analysis_task.run()."""
    mock_redaction_result = MagicMock()
    mock_redaction_result.output_path = Path(tempfile.gettempdir()) / "redacted_test.pdf"
    mock_redaction_result.filename = "redacted_test.pdf"

    mock_redaction_svc = MagicMock()
    mock_redaction_svc.anonymize_file.return_value = mock_redaction_result

    mock_adapter_inst = MagicMock()
    mock_adapter_inst.analyze_document.return_value = ocr_result

    patch_map = [
        ("app.tasks.legal_analysis._get_db_session", MagicMock(return_value=session)),
        ("app.tasks.legal_analysis._extract_tesseract_words", MagicMock(return_value=[])),
        (
            "app.tasks.legal_analysis._perform_legal_analysis",
            MagicMock(return_value={"summary": "", "errors": [], "applicable_laws": []}),
        ),
        (
            "app.services.redaction_service.RedactionService",
            MagicMock(return_value=mock_redaction_svc),
        ),
        ("app.adapters.azure_ocr.AzureOCRAdapter", MagicMock(return_value=mock_adapter_inst)),
        (
            "app.services.formal_validation.FormalDocumentExtractor",
            MagicMock(return_value=extractor_inst),
        ),
        (
            "app.services.validation.RuleEngineService",
            MagicMock(return_value=MagicMock(run=MagicMock(return_value=[]))),
        ),
        ("app.tasks.analysis._load_rules_for_document_type", MagicMock(return_value=(None, []))),
        ("app.tasks.analysis._upsert_report", MagicMock()),
        ("shutil.move", MagicMock()),
        # Prevent read_bytes() failing because redacted file doesn't exist on disk
        ("pathlib.Path.read_bytes", MagicMock(return_value=b"%PDF-1.4 test")),
        ("app.services.classification.ClassificationService", classifier_cls),
    ]

    with ExitStack() as stack:
        for target, mock_obj in patch_map:
            stack.enter_context(patch(target, mock_obj))

        from app.tasks.legal_analysis import run_legal_analysis_task

        result: dict[str, Any] = run_legal_analysis_task.run(ANALYSIS_UUID, DOCUMENT_UUID)
        return result


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_classification_called_when_text_present() -> None:
    """ClassificationService.classify() must be invoked when OCR yields text."""
    ocr_text = "Faktura VAT FV/2024/03/118 nabywca NIP netto VAT 23% brutto"
    ocr_result = _make_ocr_result(ocr_text)
    extraction = _make_extraction(DocumentType.UNKNOWN, ocr_text, 0.4)

    session = MagicMock()
    tmp_path = _make_temp_pdf()
    try:
        fake_doc, fake_analysis = _build_db_objects(session, tmp_path)

        mock_extractor_inst = MagicMock()
        mock_extractor_inst.extract.return_value = extraction

        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = (DocumentType.INVOICE, 0.95, {"invoice": 0.95})
        mock_classifier_cls = MagicMock(return_value=mock_classifier)

        result = _run_task_with_patches(
            ocr_result, mock_extractor_inst, mock_classifier_cls, session
        )

        mock_classifier.classify.assert_called_once_with(ocr_text)
        assert fake_analysis.detected_document_type == DocumentType.INVOICE.value
        assert fake_analysis.classification_confidence == 0.95
        assert fake_doc.suggested_document_type == DocumentType.INVOICE
        assert result["status"] == "success"
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


def test_classification_fallback_when_classifier_raises() -> None:
    """When ClassificationService raises, task falls back to FormalDocumentExtractor result."""
    ocr_text = "some document text"
    ocr_result = _make_ocr_result(ocr_text)
    extraction = _make_extraction(DocumentType.CONTRACT, ocr_text, 0.7)

    session = MagicMock()
    tmp_path = _make_temp_pdf()
    try:
        fake_doc, fake_analysis = _build_db_objects(session, tmp_path)

        mock_extractor_inst = MagicMock()
        mock_extractor_inst.extract.return_value = extraction

        mock_classifier = MagicMock()
        mock_classifier.classify.side_effect = RuntimeError("model not loaded")
        mock_classifier_cls = MagicMock(return_value=mock_classifier)

        result = _run_task_with_patches(
            ocr_result, mock_extractor_inst, mock_classifier_cls, session
        )

        mock_classifier.classify.assert_called_once()
        assert fake_analysis.detected_document_type == DocumentType.CONTRACT.value
        assert fake_analysis.classification_confidence == 0.7
        assert fake_doc.suggested_document_type == DocumentType.CONTRACT
        assert result["status"] == "success"
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()


def test_classification_skipped_when_no_text() -> None:
    """When OCR produces no text, ClassificationService must NOT be instantiated."""
    ocr_text = ""
    ocr_result = _make_ocr_result(ocr_text)
    extraction = _make_extraction(DocumentType.UNKNOWN, ocr_text, 0.0)

    session = MagicMock()
    tmp_path = _make_temp_pdf()
    try:
        fake_doc, fake_analysis = _build_db_objects(session, tmp_path)

        mock_extractor_inst = MagicMock()
        mock_extractor_inst.extract.return_value = extraction

        mock_classifier_cls = MagicMock()

        _run_task_with_patches(ocr_result, mock_extractor_inst, mock_classifier_cls, session)

        mock_classifier_cls.assert_not_called()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
