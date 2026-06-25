"""
Celery task: complete OCR pipeline: local OCR + redaction + Azure OCR.

Steps:
1. Load document from DB
2. Run RedactionService (local OCR + sensitive data masking)
3. Save redacted file
4. Trigger Azure Document Intelligence OCR on redacted file
5. Save Azure OCR results to DB
"""

import shutil
import uuid
from pathlib import Path
from typing import Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _get_db_session() -> Session:
    """Create a synchronous DB session for Celery workers."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    return sessionmaker(bind=engine)()


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.full_ocr.run_full_ocr_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_full_ocr_task(self: Task, analysis_id: str, document_id: str) -> dict[str, Any]:
    """
    Complete OCR pipeline: local OCR + redaction + Azure OCR.

    Steps:
    1. Load analysis + document from DB
    2. Set status to OCR_IN_PROGRESS
    3. Run RedactionService (local Tesseract OCR + pixel level masking)
    4. Save redacted file to storage
    5. Update document storage_path to redacted file
    6. Trigger Azure Document Intelligence OCR on redacted file
    """
    from app.enums.analysis import AnalysisStatus

    log = logger.bind(analysis_id=analysis_id, document_id=document_id, task_id=self.request.id)
    log.info("Local OCR redaction task started")

    session = _get_db_session()
    analysis = None

    try:
        from app.models.document import Document
        from app.models.document_analysis import DocumentAnalysis

        # 1. Load analysis from DB
        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            log.warning("Analysis not found, retrying...")
            raise self.retry(exc=FileNotFoundError(f"Analysis {analysis_id} not found")) from None

        # 2. Set status to IN_PROGRESS
        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        session.commit()
        log.info("Status updated to OCR_IN_PROGRESS")

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

        # 4. Save redacted file to permanent storage (use shutil.move for cross-filesystem)
        redacted_storage_path = file_path.parent / f"redacted_{file_path.name}"
        shutil.move(str(redaction_result.output_path), str(redacted_storage_path))

        log.info("Redacted file saved", path=str(redacted_storage_path))

        # 5. Update document with redacted file path (keep original for reference)
        document.storage_path = str(redacted_storage_path)
        session.commit()

        # 6. Trigger Azure OCR on redacted file
        from app.tasks.azure_ocr import run_azure_ocr_task

        run_azure_ocr_task.delay(analysis_id)
        log.info("Azure OCR task queued")

    except Exception as exc:
        log.exception("Local OCR redaction task failed")
        if analysis:
            analysis.status = AnalysisStatus.OCR_FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc) from exc

    else:
        return {"status": "success", "analysis_id": analysis_id}

    finally:
        session.close()
