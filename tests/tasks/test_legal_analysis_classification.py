"""
Tests verifying that legal_analysis task includes TF-IDF + LR classification step.

Some dependencies are imported at module level in app.tasks.legal_analysis
(FormalDocumentExtractor, RuleEngineService, rule/report helpers, lock/status helpers)
and some locally inside the task (ClassificationService, RedactionService,
AzureOCRAdapter). _target() patches the name where it is actually looked up.
"""

from __future__ import annotations

import contextlib
import tempfile
import uuid
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import app.tasks.legal_analysis as task_module
from app.enums.document import DocumentType

ANALYSIS_UUID = "00000000-0000-0000-0000-000000000001"
DOCUMENT_UUID = "00000000-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _target(names: tuple[str, ...], source_module: str | None = None) -> str:
    """Patch target: task module if it holds the name, otherwise the source module."""
    for name in names:
        if hasattr(task_module, name):
            return f"app.tasks.legal_analysis.{name}"
    if source_module is None:
        raise AttributeError(f"None of {names} found in app.tasks.legal_analysis")
    return f"{source_module}.{names[0]}"


def _make_extraction(
    document_type: DocumentType = DocumentType.UNKNOWN,
    text: str = "",
    confidence: float = 0.5,
) -> Any:
    fields = {"has_text": bool(text), "document_text": text}
    return SimpleNamespace(document_type=document_type, confidence=confidence, fields=fields)


def _make_ocr_result(text: str = "invoice content netto VAT brutto") -> Any:
    return SimpleNamespace(
        raw={"content": text}, text_content=text, pages=[], page_count=1, provider="azure"
    )


def _make_temp_pdf() -> Path:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(b"%PDF-1.4 test")
        return Path(tmp.name)


def _build_db_objects(session: MagicMock, tmp_path: Path) -> tuple[MagicMock, MagicMock]:
    """Build fake Document + DocumentAnalysis and wire session.get to return them."""
    from app.enums.analysis import AnalysisStatus, ProcessingStage

    fake_doc = MagicMock()
    fake_doc.storage_path = str(tmp_path)
    fake_doc.original_filename = "test.pdf"
    fake_doc.mime_type = "application/pdf"
    fake_doc.document_type = DocumentType.UNKNOWN

    fake_analysis = MagicMock()
    fake_analysis.id = ANALYSIS_UUID
    fake_analysis.document_id = uuid.UUID(DOCUMENT_UUID)
    fake_analysis.status = AnalysisStatus.PENDING
    fake_analysis.processing_stage = ProcessingStage.PENDING
    fake_analysis.processing_step = 0
    fake_analysis.ocr_raw_result = None
    fake_analysis.tesseract_words = []
    fake_analysis.legal_analysis_result = None
    # fields read by checkpoint() / cancellation_requested() / pending-task handoff
    fake_analysis.task_id = None
    fake_analysis.cancel_requested_at = None
    fake_analysis.pipeline_params = {}

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
    fake_analysis: MagicMock,
) -> dict[str, Any]:
    """Apply all required patches and call run_legal_analysis_task.run()."""
    mock_redaction_result = MagicMock()
    mock_redaction_result.output_path = Path(tempfile.gettempdir()) / "redacted_test.pdf"
    mock_redaction_result.filename = "redacted_test.pdf"

    mock_redaction_svc = MagicMock()
    mock_redaction_svc.anonymize_file.return_value = mock_redaction_result

    mock_adapter_inst = MagicMock()
    mock_adapter_inst.analyze_paged.return_value = ocr_result

    # Works whether the task calls FormalDocumentExtractor.extract(...) (static/classmethod)
    # or FormalDocumentExtractor().extract(...).
    mock_extractor_cls = MagicMock(return_value=extractor_inst)
    mock_extractor_cls.extract = extractor_inst.extract

    patch_map = [
        ("app.tasks.legal_analysis._get_db_session", MagicMock(return_value=session)),
        (_target(("_lock_analysis", "lock_analysis")), MagicMock(return_value=fake_analysis)),
        (_target(("_update_document_status", "update_document_status")), MagicMock()),
        ("app.tasks.legal_analysis._extract_tesseract_words", MagicMock(return_value=[])),
        (
            "app.tasks.legal_analysis._perform_legal_analysis",
            MagicMock(return_value={"summary": "", "errors": [], "applicable_laws": []}),
        ),
        ("app.tasks.legal_analysis._warm_page_previews", MagicMock()),
        (
            _target(("RedactionService",), "app.services.redaction_service"),
            MagicMock(return_value=mock_redaction_svc),
        ),
        (
            _target(("AzureOCRAdapter",), "app.adapters.azure_ocr"),
            MagicMock(return_value=mock_adapter_inst),
        ),
        (
            _target(("FormalDocumentExtractor",), "app.services.formal_validation"),
            mock_extractor_cls,
        ),
        (
            _target(("RuleEngineService",), "app.services.validation"),
            MagicMock(run=MagicMock(return_value=[])),
        ),
        (
            _target(
                ("_load_rules_for_document_type", "load_rules_for_document_type"),
                "app.tasks.analysis",
            ),
            MagicMock(return_value=(None, [])),
        ),
        (_target(("_upsert_report", "upsert_report"), "app.tasks.analysis"), MagicMock()),
        ("shutil.move", MagicMock()),
        # Prevent read_bytes() failing because redacted file doesn't exist on disk
        ("pathlib.Path.read_bytes", MagicMock(return_value=b"%PDF-1.4 test")),
        (_target(("ClassificationService",), "app.services.classification"), classifier_cls),
    ]

    with ExitStack() as stack:
        for target, mock_obj in patch_map:
            stack.enter_context(patch(target, mock_obj))

        result: dict[str, Any] = task_module.run_legal_analysis_task.run(
            ANALYSIS_UUID, DOCUMENT_UUID
        )
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
        _, fake_analysis = _build_db_objects(session, tmp_path)

        mock_extractor_inst = MagicMock()
        mock_extractor_inst.extract.return_value = extraction

        mock_classifier = MagicMock()
        mock_classifier.classify.return_value = (DocumentType.INVOICE, 0.95, {"invoice": 0.95})
        mock_classifier_cls = MagicMock(return_value=mock_classifier)

        result = _run_task_with_patches(
            ocr_result, mock_extractor_inst, mock_classifier_cls, session, fake_analysis
        )

        assert result["status"] == "success", result
        mock_classifier.classify.assert_called_once_with(ocr_text)
        assert fake_analysis.detected_document_type == DocumentType.INVOICE.value
        assert fake_analysis.classification_confidence == 0.95
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
        _, fake_analysis = _build_db_objects(session, tmp_path)

        mock_extractor_inst = MagicMock()
        mock_extractor_inst.extract.return_value = extraction

        mock_classifier = MagicMock()
        mock_classifier.classify.side_effect = RuntimeError("model not loaded")
        mock_classifier_cls = MagicMock(return_value=mock_classifier)

        result = _run_task_with_patches(
            ocr_result, mock_extractor_inst, mock_classifier_cls, session, fake_analysis
        )

        assert result["status"] == "success", result
        mock_classifier.classify.assert_called_once()
        assert fake_analysis.detected_document_type == DocumentType.CONTRACT.value
        assert fake_analysis.classification_confidence == 0.7
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
        _, fake_analysis = _build_db_objects(session, tmp_path)

        mock_extractor_inst = MagicMock()
        mock_extractor_inst.extract.return_value = extraction

        mock_classifier_cls = MagicMock()

        result = _run_task_with_patches(
            ocr_result, mock_extractor_inst, mock_classifier_cls, session, fake_analysis
        )

        # Guards against the task bailing out early (e.g. stale_task) and passing vacuously.
        assert result["status"] == "success", result
        mock_classifier_cls.assert_not_called()
        assert fake_analysis.detected_document_type == DocumentType.UNKNOWN.value
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
