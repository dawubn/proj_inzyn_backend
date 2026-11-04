"""
Celery task: full OCR pipeline and legal processing via Azure LLM.

Cancellation is checked between stages. Running external calls are allowed
to return before cancellation is confirmed and the owner's slot is released.
"""

import shutil
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.core.exceptions import OCRServiceError
from app.enums.analysis import (
    TERMINAL_STATUSES,
    AnalysisStatus,
    ProcessingStage,
    ValidationSeverity,
)
from app.enums.document import DocumentStatus, DocumentType
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.schemas.analysis_report import ValidationIssue
from app.services.formal_validation import FormalDocumentExtractor
from app.services.validation import RuleEngineService
from app.tasks.analysis import (
    _add_profile_availability_warning,
    _coerce_document_type,
    _load_rules_for_document_type,
    _upsert_report,
)
from app.tasks.celery_app import celery_app
from app.tasks.local_ocr import (
    _cancellation_requested,
    _finish_cancellation,
    _lock_analysis,
    _update_document_status,
)

logger = structlog.get_logger(__name__)

_FUZZY_MATCH_THRESHOLD = 0.7


class _ProcessingStopped(Exception):
    """Internal control flow; must not trigger a Celery retry."""

    def __init__(self, processing_status: str) -> None:
        super().__init__(processing_status)
        self.processing_status = processing_status


def _extract_tesseract_words(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract Tesseract words with bounding boxes, processing one page at a time."""
    import fitz

    from app.adapters.local_ocr import LocalOCRAdapter

    adapter = LocalOCRAdapter()
    all_words: list[dict[str, Any]] = []
    word_id = 0

    try:
        with fitz.open(str(pdf_path)) as source:
            if source.is_pdf:
                pdf_document = None
                doc = source
            else:
                # PyMuPDF image documents must be converted before using
                # PDF-style page rendering for multipage TIFF files.
                pdf_document = fitz.open(
                    stream=source.convert_to_pdf(),
                    filetype="pdf",
                )
                doc = pdf_document

            try:
                for page_num in range(len(doc)):
                    page = doc[page_num]
                    pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
                    png_bytes = pix.tobytes("png")

                    with tempfile.NamedTemporaryFile(
                        suffix=".png",
                        delete=False,
                    ) as tmp:
                        tmp.write(png_bytes)
                        tmp_path = Path(tmp.name)

                    try:
                        words_per_page, _ = adapter.ocr_image(tmp_path)

                        for words in words_per_page:
                            for word in words:
                                all_words.append(
                                    {
                                        "id": f"w{word_id}",
                                        "text": word["tekst"],
                                        "bbox": {
                                            "x": word["x"],
                                            "y": word["y"],
                                            "width": word["szerokosc"],
                                            "height": word["wysokosc"],
                                        },
                                        "page": page_num + 1,
                                    }
                                )
                                word_id += 1
                    finally:
                        tmp_path.unlink(missing_ok=True)
            finally:
                if pdf_document is not None:
                    pdf_document.close()

    except Exception as exc:
        raise OCRServiceError(f"Tesseract extraction failed: {exc}") from exc

    return all_words


def _map_error_to_bbox(
    text_reference: str,
    tesseract_words: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a Tesseract word matching the supplied text reference."""
    from difflib import SequenceMatcher

    text_ref_lower = text_reference.lower()
    best_match = None
    best_ratio = 0.0

    for word in tesseract_words:
        ratio = SequenceMatcher(
            None,
            text_ref_lower,
            word["text"].lower(),
        ).ratio()

        if ratio > best_ratio and ratio > _FUZZY_MATCH_THRESHOLD:
            best_ratio = ratio
            best_match = word

    return best_match


def _get_db_session() -> Session:
    """Create a synchronous DB session for Celery workers."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    return sessionmaker(bind=engine)()


def _checkpoint(
    session: Session,
    analysis_id: uuid.UUID,
    task_id: str,
) -> DocumentAnalysis:
    """
    Refresh and lock processing state.

    The caller must commit or roll back before starting expensive work.
    """
    analysis = _lock_analysis(session, analysis_id)

    if analysis is None:
        session.rollback()
        raise _ProcessingStopped("not_found")

    if analysis.status in TERMINAL_STATUSES:
        processing_status = str(analysis.status)
        session.rollback()
        raise _ProcessingStopped(processing_status)

    if analysis.task_id and analysis.task_id != task_id:
        session.rollback()
        raise _ProcessingStopped("stale_task")

    if _cancellation_requested(analysis):
        _finish_cancellation(session, analysis)
        raise _ProcessingStopped("cancelled")

    if analysis.status == AnalysisStatus.QUEUED:
        session.rollback()
        raise _ProcessingStopped("not_dispatched")

    return analysis


def _build_legal_analysis_prompt(ocr_text: str, filename: str) -> str:
    """Build a structured prompt for Azure LLM legal analysis."""
    return f"""Jesteś prawnikiem specjalizującym się w prawie polskim. Przeanalizuj poniższy dokument:

Dokument: {filename}

---
{ocr_text}
---

Proszę przeanalizować dokument z perspektywy prawa polskiego i udzielić:

1. **Podsumowanie**: Krótkie podsumowanie celu i zawartości dokumentu
2. **Błędy/Braki**: Co jest złego, brakuje lub jest nieprawidłowe w dokumencie.
DLA KAŻDEGO BŁĘDU podaj dokładny fragment tekstu z dokumentu do którego się odnosi (text_reference).
3. **Przepisy prawne**: Jakie przepisy, regulacje lub normy prawne mają zastosowanie do tego dokumentu

Odpowiedź sformatuj jako JSON z dokładnie tymi kluczami:
{{
  "summary": "...",
  "errors": [
    {{
      "issue": "opis błędu/braku",
      "text_reference": "dokładny fragment tekstu z dokumentu do którego się odnosi",
      "severity": "critical|high|medium"
    }}
  ],
  "applicable_laws": [
    {{
      "law": "nazwa przepisu",
      "description": "opis zastosowania",
      "reference": "artykuł/paragraf"
    }}
  ]
}}

WAŻNE:
- text_reference MUSI być dokładnym fragmentem z dokumentu
- Jeśli brakuje czegoś (np. daty), wpisz "brak" lub wskaż gdzie powinna być
- Odpowiadaj TYLKO poprawnym JSON, bez dodatkowego tekstu
- Dokument zawiera elementy utajnione (np. [UTAJNIONO]), które oznaczają usunięte dane wrażliwe. Nie uwzględniaj tego w analizie błędów."""


def _classify_document(
    text_content: str,
    fallback_type: DocumentType,
    fallback_confidence: float,
    log: Any,
) -> tuple[DocumentType, float]:
    """Run the classifier and retain the extraction fallback on failure."""
    if not text_content:
        return fallback_type, fallback_confidence

    from app.services.classification import ClassificationService

    try:
        document_type, confidence, _ = ClassificationService().classify(text_content)
        log.info(
            "Document classified",
            document_type=document_type,
            confidence=round(confidence, 4),
        )
    except Exception as exc:
        log.warning("Classification fallback used", reason=str(exc))
        return fallback_type, fallback_confidence
    else:
        return document_type, confidence


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.legal_analysis.run_legal_analysis_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_legal_analysis_task(
    self: Task,
    analysis_id: str,
    document_id: str,
) -> dict[str, Any]:
    """Run OCR, redaction, validation and LLM processing with cancellation."""
    from app.adapters.azure_ocr import AzureOCRAdapter
    from app.services.redaction_service import RedactionService

    log = logger.bind(
        analysis_id=analysis_id,
        document_id=document_id,
        task_id=self.request.id,
    )
    log.info("Legal processing started")

    analysis_uuid = uuid.UUID(analysis_id)
    document_uuid = uuid.UUID(document_id)
    task_id = str(self.request.id)
    session = _get_db_session()

    temporary_output: Path | None = None
    redacted_storage_path: Path | None = None

    try:
        analysis = _checkpoint(session, analysis_uuid, task_id)

        if analysis.document_id != document_uuid:
            raise ValueError("Document does not match the processing request")

        document = session.get(Document, document_uuid)
        if document is None:
            raise FileNotFoundError(f"Document {document_id} not found")

        file_path = Path(document.storage_path)
        original_filename = document.original_filename
        content_type = document.mime_type
        fallback_type = _coerce_document_type(document.document_type)

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        analysis.task_id = task_id
        analysis.status = AnalysisStatus.IN_PROGRESS
        analysis.processing_stage = ProcessingStage.LOCAL_OCR
        analysis.processing_step = 1
        analysis.started_at = analysis.started_at or datetime.now(UTC)
        analysis.error_message = None
        document.status = DocumentStatus.PROCESSING

        params = dict(analysis.pipeline_params or {})
        pending_task = params.get("pending_task")
        if (
            isinstance(pending_task, dict)
            and pending_task.get("task_id") == task_id
        ):
            params.pop("pending_task", None)
            analysis.pipeline_params = params

        session.commit()

        # Local OCR and redaction run without holding the owner lock.
        redaction_result = RedactionService().anonymize_file(
            input_path=file_path,
            original_filename=original_filename,
            content_type=content_type,
        )
        temporary_output = Path(redaction_result.output_path)

        analysis = _checkpoint(session, analysis_uuid, task_id)
        analysis.processing_stage = ProcessingStage.REDACTION
        analysis.processing_step = 2
        session.commit()

        # Keep the original document intact; the artifact belongs to this attempt.
        redacted_storage_path = file_path.parent / (
            f"redacted_{analysis_uuid}_{uuid.uuid4().hex}.tiff"
        )
        shutil.move(str(temporary_output), str(redacted_storage_path))
        temporary_output = None

        # Check again before the additional Tesseract pass.
        _checkpoint(session, analysis_uuid, task_id)
        session.commit()

        tesseract_words = _extract_tesseract_words(redacted_storage_path)

        analysis = _checkpoint(session, analysis_uuid, task_id)
        analysis.tesseract_words = tesseract_words
        analysis.processing_stage = ProcessingStage.AZURE_OCR
        analysis.processing_step = 3
        session.commit()

        log.info(
            "Tesseract extraction completed",
            word_count=len(tesseract_words),
        )

        adapter = AzureOCRAdapter()
        ocr_result = adapter.analyze_document(
            redacted_storage_path.read_bytes(),
            "image/tiff",
        )

        analysis = _checkpoint(session, analysis_uuid, task_id)
        analysis.ocr_raw_result = ocr_result.raw
        analysis.ocr_provider = ocr_result.provider
        analysis.status = AnalysisStatus.CLASSIFYING
        session.commit()

        log.info("Azure OCR completed", page_count=ocr_result.page_count)

        extraction = FormalDocumentExtractor().extract(
            ocr_result.raw,
            fallback_document_type=fallback_type,
        )
        text_content = str(extraction.fields.get("document_text", "")).strip()
        document_type, classification_confidence = _classify_document(
            text_content,
            extraction.document_type,
            extraction.confidence,
            log,
        )

        analysis = _checkpoint(session, analysis_uuid, task_id)
        analysis.detected_document_type = document_type.value
        analysis.classification_confidence = classification_confidence
        analysis.extracted_fields = extraction.fields
        analysis.status = AnalysisStatus.VALIDATING

        document = session.get(Document, document_uuid)
        if document is None:
            raise FileNotFoundError(f"Document {document_id} not found")

        document.suggested_document_type = document_type
        session.commit()

        profile_name, rules = _load_rules_for_document_type(session, document_type)
        total_rules_count = len(rules)

        issues = RuleEngineService().run(rules, extraction.fields)
        validation_profile_available = _add_profile_availability_warning(
            issues,
            profile_name,
            rules,
            document_type,
        )

        if not extraction.fields.get("has_text"):
            issues.insert(
                0,
                ValidationIssue(
                    rule_name="Tekst dokumentu",
                    field_name="has_text",
                    severity=ValidationSeverity.ERROR,
                    message="Nie udało się odczytać tekstu dokumentu.",
                    details={"expected_field": "has_text"},
                ),
            )

        # End the rules read transaction before checking cancellation.
        session.rollback()

        analysis = _checkpoint(session, analysis_uuid, task_id)
        analysis.status = AnalysisStatus.IN_PROGRESS
        analysis.processing_stage = ProcessingStage.LLM_ANALYSIS
        analysis.processing_step = 4
        session.commit()

        ocr_text = ocr_result.text_content
        legal_analysis = _perform_legal_analysis(
            ocr_text,
            original_filename,
            log,
        )

        errors_with_bbox: list[dict[str, Any]] = []
        for error in legal_analysis.get("errors", []):
            error_copy = error.copy()
            text_ref = error.get("text_reference", "")

            if text_ref:
                bbox_match = _map_error_to_bbox(text_ref, tesseract_words)
                if bbox_match:
                    error_copy["bbox"] = bbox_match["bbox"]
                    error_copy["word_id"] = bbox_match["id"]
                    error_copy["page"] = bbox_match["page"]

            errors_with_bbox.append(error_copy)

        # A cancellation requested during the LLM call wins over result saving.
        analysis = _checkpoint(session, analysis_uuid, task_id)

        _upsert_report(
            session=session,
            analysis_id=analysis.id,
            issues=issues,
            total_rules=max(
                total_rules_count,
                1 if not extraction.fields.get("has_text") else 0,
            ),
            summary={
                "document_type": document_type.value,
                "classification_confidence": classification_confidence,
                "profile_name": profile_name,
                "validation_profile_available": validation_profile_available,
                "total_rules": total_rules_count,
                "issues": len(issues),
                "checked_fields": {
                    key: value
                    for key, value in extraction.fields.items()
                    if key.startswith("has_")
                },
                "detected_sections": extraction.fields.get("detected_sections", []),
            },
        )

        analysis.legal_analysis_result = {
            "prompt": _build_legal_analysis_prompt(ocr_text, original_filename),
            "summary": legal_analysis.get("summary", ""),
            "errors": errors_with_bbox,
            "applicable_laws": legal_analysis.get("applicable_laws", []),
        }
        analysis.status = AnalysisStatus.COMPLETED
        analysis.processing_stage = ProcessingStage.COMPLETED
        analysis.processing_step = 4
        analysis.finished_at = datetime.now(UTC)
        analysis.error_message = None

        _update_document_status(session, analysis, DocumentStatus.PROCESSED)
        session.commit()

        log.info("Legal processing completed")
        return {"status": "success", "analysis_id": analysis_id}

    except _ProcessingStopped as stopped:
        log.info(
            "Legal processing stopped",
            processing_status=stopped.processing_status,
        )
        return {
            "status": stopped.processing_status,
            "analysis_id": analysis_id,
        }

    except Exception as exc:
        session.rollback()
        log.exception("Legal processing failed")

        try:
            analysis = _checkpoint(session, analysis_uuid, task_id)
        except _ProcessingStopped as stopped:
            return {
                "status": stopped.processing_status,
                "analysis_id": analysis_id,
            }

        analysis.error_message = str(exc)

        if self.request.retries < self.max_retries:
            # Retain the owner's slot while Celery waits to retry.
            analysis.status = AnalysisStatus.IN_PROGRESS
            session.commit()
            raise self.retry(exc=exc) from exc

        analysis.status = AnalysisStatus.FAILED
        analysis.finished_at = datetime.now(UTC)

        _update_document_status(session, analysis, DocumentStatus.FAILED)
        session.commit()

        raise

    finally:
        session.close()

        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)

        if redacted_storage_path is not None:
            redacted_storage_path.unlink(missing_ok=True)


def _perform_legal_analysis(
    ocr_text: str,
    filename: str,
    log: Any,
) -> dict[str, Any]:
    """Send OCR text to Azure LLM for legal analysis."""
    import json

    from openai import AzureOpenAI

    from app.core.config import settings

    if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_KEY:
        log.warning("Azure LLM not configured, skipping legal analysis")
        return {"summary": "", "errors": [], "applicable_laws": []}

    try:
        with AzureOpenAI(
            api_key=settings.AZURE_OPENAI_KEY,
            api_version="2024-02-15-preview",
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        ) as client:
            prompt = _build_legal_analysis_prompt(ocr_text, filename)

            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )

            response_text = response.choices[0].message.content or ""

        if not response_text:
            log.warning("LLM response was empty")
            return {"summary": "", "errors": [], "applicable_laws": []}

        log.info("LLM response received", length=len(response_text))

        result = json.loads(response_text)
        if not isinstance(result, dict):
            raise ValueError("LLM response must be a JSON object")

        errors = result.get("errors", [])
        if not isinstance(errors, list):
            raise ValueError("LLM errors must be a list")

        validated_errors = []
        for error in errors:
            if isinstance(error, dict):
                validated_errors.append(
                    {
                        "issue": str(error.get("issue") or ""),
                        "text_reference": str(error.get("text_reference") or ""),
                        "severity": str(error.get("severity") or "medium"),
                    }
                )
            else:
                validated_errors.append(
                    {
                        "issue": str(error),
                        "text_reference": "",
                        "severity": "medium",
                    }
                )

        applicable_laws = result.get("applicable_laws", [])
        if not isinstance(applicable_laws, list):
            applicable_laws = []

        return {
            "summary": str(result.get("summary") or ""),
            "errors": validated_errors,
            "applicable_laws": applicable_laws,
        }

    except Exception as exc:
        log.exception("LLM analysis failed", error=str(exc))
        raise