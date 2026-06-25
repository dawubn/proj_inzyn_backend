"""
Celery task: run full OCR pipeline + legal analysis via Azure LLM.

Steps:
1. Load document from DB
2. Run RedactionService (local OCR + sensitive data masking)
3. Save redacted file
4. Trigger Azure OCR on redacted file
5. Wait for Azure OCR results
6. Send OCR text to Azure LLM for legal analysis
7. Save legal analysis results to DB
"""

import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.core.exceptions import OCRServiceError
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _extract_tesseract_words(pdf_path: Path) -> list[dict[str, Any]]:
    """Extract Tesseract words with bounding boxes from PDF (per-page, memory-efficient)."""
    import fitz

    from app.adapters.local_ocr import LocalOCRAdapter

    adapter = LocalOCRAdapter()
    all_words = []
    word_id = 0

    try:
        doc = fitz.open(str(pdf_path))
        page_count = len(doc)

        for page_num in range(page_count):
            page = doc[page_num]
            pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
            png_bytes = pix.tobytes("png")

            # Save to temp file for local_ocr to process
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(png_bytes)
                tmp_path = Path(tmp.name)

            try:
                # Extract words for this page
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

        doc.close()
    except Exception as exc:
        raise OCRServiceError(f"Tesseract extraction failed: {exc}") from exc

    return all_words


_FUZZY_MATCH_THRESHOLD = 0.7


def _map_error_to_bbox(
    text_reference: str, tesseract_words: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find Tesseract bbox for error text_reference (fuzzy matching)."""
    from difflib import SequenceMatcher

    text_ref_lower = text_reference.lower()
    best_match = None
    best_ratio = 0.0

    # Try to find matching words
    for word in tesseract_words:
        ratio = SequenceMatcher(None, text_ref_lower, word["text"].lower()).ratio()
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
    }},
    ...
  ],
  "applicable_laws": [
    {{"law": "nazwa przepisu", "description": "opis zastosowania",
      "reference": "artykuł/paragraf"}},
    ...
  ]
}}

WAŻNE:
- text_reference MUSI być dokładnym fragmentem z dokumentu
- Jeśli brakuje czegoś (np. daty), wpisz "brak" lub wskaż gdzie powinna być
- Odpowiadaj TYLKO poprawnym JSON, bez dodatkowego tekstu
- Dokument zawiera elementy utajnione (np. [UTAJNIONO]), które oznaczają usunięte dane wrażliwe. Nie uwzględniaj tego w analizie błędów."""


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.legal_analysis.run_legal_analysis_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_legal_analysis_task(  # noqa: PLR0915
    self: Task, analysis_id: str, document_id: str
) -> dict[str, Any]:
    """
    Complete OCR pipeline + legal analysis: local OCR, redaction, Azure OCR, LLM analysis.

    Steps:
    1. Load analysis + document from DB
    2. Run RedactionService (local OCR + masking)
    3. Save redacted file
    4. Trigger Azure OCR
    5. Retrieve OCR results
    6. Send to Azure LLM for legal analysis
    7. Save legal_analysis_result to DB
    """
    from app.enums.analysis import AnalysisStatus, ProcessingStage, ValidationSeverity
    from app.models.document import Document
    from app.models.document_analysis import DocumentAnalysis
    from app.schemas.analysis_report import ValidationIssue
    from app.services.formal_validation import FormalDocumentExtractor
    from app.services.validation import RuleEngineService
    from app.tasks.analysis import (
        _coerce_document_type,
        _load_rules_for_document_type,
        _upsert_report,
    )

    log = logger.bind(analysis_id=analysis_id, document_id=document_id, task_id=self.request.id)
    log.info("Legal analysis task started")

    session = _get_db_session()
    analysis = None

    try:
        # 1. Load analysis from DB
        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            log.warning("Analysis not found, retrying...")
            raise self.retry(exc=FileNotFoundError(f"Analysis {analysis_id} not found")) from None

        # 2. Set status to IN_PROGRESS - LOCAL OCR (step 1/4)
        analysis.status = AnalysisStatus.IN_PROGRESS
        analysis.processing_stage = ProcessingStage.LOCAL_OCR
        analysis.processing_step = 1
        session.commit()
        log.info("Status updated to LOCAL_OCR")

        # Load document
        document = session.get(Document, uuid.UUID(document_id))
        if not document:
            raise FileNotFoundError(f"Document {document_id} not found in DB")

        # Check file exists
        file_path = Path(document.storage_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # 3. Run RedactionService (local OCR + masking)
        from app.services.redaction_service import RedactionService

        redaction_svc = RedactionService()
        redaction_result = redaction_svc.anonymize_file(
            input_path=file_path,
            original_filename=document.original_filename,
            content_type=document.mime_type,
        )

        log.info(
            "Redaction completed",
            output_filename=redaction_result.filename,
            output_path=str(redaction_result.output_path),
        )

        # Update progress - REDACTION (step 2/4)
        analysis.processing_stage = ProcessingStage.REDACTION
        analysis.processing_step = 2
        session.commit()

        # 4. Save redacted file to permanent storage
        redacted_storage_path = file_path.parent / f"redacted_{file_path.name}"
        shutil.move(str(redaction_result.output_path), str(redacted_storage_path))

        log.info("Redacted file saved", path=str(redacted_storage_path))

        # 5. Update document with redacted file path
        document.storage_path = str(redacted_storage_path)
        session.commit()

        # 5.5 Extract Tesseract words with bounding boxes
        log.info("Extracting Tesseract words")
        tesseract_words = _extract_tesseract_words(redacted_storage_path)
        analysis.tesseract_words = tesseract_words
        session.commit()

        log.info("Tesseract extraction completed", word_count=len(tesseract_words))

        # 6. Run Azure OCR on redacted file - AZURE OCR (step 3/4)
        analysis.processing_stage = ProcessingStage.AZURE_OCR
        analysis.processing_step = 3
        session.commit()

        from app.adapters.azure_ocr import AzureOCRAdapter

        adapter = AzureOCRAdapter()
        ocr_result = adapter.analyze_document(
            redacted_storage_path.read_bytes(), document.mime_type
        )

        # 7. Save OCR results
        analysis.ocr_raw_result = ocr_result.raw
        analysis.ocr_provider = ocr_result.provider
        session.commit()

        log.info("Azure OCR completed", page_count=ocr_result.page_count)

        extraction = FormalDocumentExtractor().extract(
            ocr_result.raw,
            fallback_document_type=_coerce_document_type(document.document_type),
        )
        analysis.detected_document_type = extraction.document_type.value
        analysis.classification_confidence = extraction.confidence
        analysis.extracted_fields = extraction.fields
        document.document_type = extraction.document_type

        profile_name, rules = _load_rules_for_document_type(session, extraction.document_type)
        issues = RuleEngineService().run(rules, extraction.fields)
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
        _upsert_report(
            session=session,
            analysis_id=analysis.id,
            issues=issues,
            total_rules=max(len(rules), 1 if not extraction.fields.get("has_text") else 0),
            summary={
                "document_type": extraction.document_type.value,
                "classification_confidence": extraction.confidence,
                "profile_name": profile_name,
                "total_rules": len(rules),
                "issues": len(issues),
                "checked_fields": {
                    key: value for key, value in extraction.fields.items() if key.startswith("has_")
                },
                "detected_sections": extraction.fields.get("detected_sections", []),
            },
        )
        session.commit()

        # 8. Use OCR text for legal analysis - LLM ANALYSIS (step 4/4)
        analysis.processing_stage = ProcessingStage.LLM_ANALYSIS
        analysis.processing_step = 4
        session.commit()

        ocr_text = ocr_result.text_content

        # 9. Send to Azure LLM for legal analysis
        legal_analysis = _perform_legal_analysis(ocr_text, document.original_filename, log)

        # 10. Map LLM errors to Tesseract bbox
        errors_with_bbox = []
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

        # 11. Save legal analysis results and mark as completed
        analysis.legal_analysis_result = {
            "prompt": _build_legal_analysis_prompt(ocr_text, document.original_filename),
            "summary": legal_analysis.get("summary", ""),
            "errors": errors_with_bbox,
            "applicable_laws": legal_analysis.get("applicable_laws", []),
        }
        analysis.status = AnalysisStatus.COMPLETED
        analysis.processing_stage = ProcessingStage.COMPLETED
        session.commit()

        log.info("Legal analysis completed and saved")

    except Exception as exc:
        log.exception("Legal analysis task failed")
        if analysis:
            analysis.status = AnalysisStatus.OCR_FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc) from exc

    else:
        return {"status": "success", "analysis_id": analysis_id}

    finally:
        session.close()


def _perform_legal_analysis(
    ocr_text: str, filename: str, log: structlog.PrintLogger
) -> dict[str, Any]:
    """Send OCR text to Azure LLM for legal analysis."""
    import json

    from openai import AzureOpenAI

    from app.core.config import settings

    if not settings.AZURE_OPENAI_ENDPOINT or not settings.AZURE_OPENAI_KEY:
        log.warning("Azure LLM not configured, skipping legal analysis")
        return {"summary": "", "errors": [], "applicable_laws": []}

    try:
        client = AzureOpenAI(
            api_key=settings.AZURE_OPENAI_KEY,
            api_version="2024-02-15-preview",
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
        )

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

        # Parse JSON response
        result = json.loads(response_text)

        # Validate errors have text_reference
        errors = result.get("errors", [])
        validated_errors = []
        for error in errors:
            if isinstance(error, dict):
                validated_errors.append(
                    {
                        "issue": error.get("issue", ""),
                        "text_reference": error.get("text_reference", ""),
                        "severity": error.get("severity", "medium"),
                    }
                )
            else:
                # Handle case where error is just a string
                validated_errors.append(
                    {
                        "issue": str(error),
                        "text_reference": "",
                        "severity": "medium",
                    }
                )

        return {
            "summary": result.get("summary", ""),
            "errors": validated_errors,
            "applicable_laws": result.get("applicable_laws", []),
        }

    except Exception as exc:
        log.exception("LLM analysis failed", error=str(exc))
        return {
            "summary": "",
            "errors": [],
            "applicable_laws": [],
        }
