"""
Celery task: local OCR + redaction, followed by Azure OCR.

The processing slot remains occupied across the pipeline.
Cancellation is checked before redaction and before handing off to Azure OCR.
"""

import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.enums.analysis import TERMINAL_STATUSES, AnalysisStatus, ProcessingStage
from app.enums.document import DocumentStatus
from app.models.document import Document
from app.services.redaction_service import RedactionService
from app.tasks.celery_app import celery_app
from app.tasks.local_ocr import (
    _cancellation_requested,
    _finish_cancellation,
    _lock_analysis,
    _update_document_status,
)

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
def run_full_ocr_task(
    self: Task,
    analysis_id: str,
    document_id: str,
) -> dict[str, Any]:
    """Run redaction and hand off to Azure OCR without releasing the slot."""
    log = logger.bind(
        analysis_id=analysis_id,
        document_id=document_id,
        task_id=self.request.id,
    )
    log.info("Full OCR processing started")

    analysis_uuid = uuid.UUID(analysis_id)
    document_uuid = uuid.UUID(document_id)
    session = _get_db_session()

    temporary_output: Path | None = None
    uncommitted_output: Path | None = None

    try:
        analysis = _lock_analysis(session, analysis_uuid)

        if analysis is None:
            session.rollback()
            return {"status": "error", "detail": "analysis_not_found"}

        if analysis.document_id != document_uuid:
            raise ValueError("Document does not match the processing request")

        if analysis.status in TERMINAL_STATUSES:
            result = {"status": str(analysis.status), "analysis_id": analysis_id}
            session.rollback()
            return result

        if analysis.task_id and analysis.task_id != self.request.id:
            session.rollback()
            return {"status": "ignored", "detail": "stale_task"}

        if _cancellation_requested(analysis):
            _finish_cancellation(session, analysis)
            return {"status": "cancelled", "analysis_id": analysis_id}

        if analysis.status == AnalysisStatus.QUEUED:
            session.rollback()
            return {"status": "ignored", "detail": "not_dispatched"}

        document = session.get(Document, document_uuid)
        if document is None:
            raise FileNotFoundError(f"Document {document_id} not found")

        file_path = Path(document.storage_path)
        original_filename = document.original_filename
        content_type = document.mime_type

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        analysis.task_id = self.request.id
        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        analysis.processing_stage = ProcessingStage.REDACTION
        analysis.processing_step = 1
        analysis.started_at = analysis.started_at or datetime.now(UTC)
        analysis.error_message = None
        document.status = DocumentStatus.PROCESSING

        # Do not retain the owner lock while OCR is running.
        session.commit()

        redaction_svc = RedactionService()
        redaction_result = redaction_svc.anonymize_file(
            input_path=file_path,
            original_filename=original_filename,
            content_type=content_type,
        )
        temporary_output = Path(redaction_result.output_path)

        log.info("Redaction completed", output_filename=redaction_result.filename)

        # Cancellation may have been requested during redaction.
        analysis = _lock_analysis(session, analysis_uuid)

        if analysis is None:
            session.rollback()
            return {"status": "error", "detail": "analysis_not_found"}

        if analysis.status in TERMINAL_STATUSES:
            result = {"status": str(analysis.status), "analysis_id": analysis_id}
            session.rollback()
            return result

        if analysis.task_id and analysis.task_id != self.request.id:
            session.rollback()
            return {"status": "ignored", "detail": "stale_task"}

        if _cancellation_requested(analysis):
            _finish_cancellation(session, analysis)
            return {"status": "cancelled", "analysis_id": analysis_id}

        document = session.get(Document, document_uuid)
        if document is None:
            raise FileNotFoundError(f"Document {document_id} not found")

        # A unique path avoids overwriting another processing request's output.
        redacted_storage_path = file_path.parent / (
            f"redacted_{analysis_uuid}_{uuid.uuid4().hex}{file_path.suffix}"
        )
        uncommitted_output = redacted_storage_path
        shutil.move(str(temporary_output), str(redacted_storage_path))
        temporary_output = None

        document.storage_path = str(redacted_storage_path)
        analysis.processing_step = 2
        analysis.processing_stage = ProcessingStage.AZURE_OCR

        # Persist the handoff before publishing the next task.
        # The dispatcher will publish requests with pending_task metadata.
        next_task_id = str(uuid.uuid4())
        params = dict(analysis.pipeline_params or {})
        params["pending_task"] = {
            "name": "app.tasks.azure_ocr.run_azure_ocr_task",
            "task_id": next_task_id,
            "args": [analysis_id],
            "queue": "ocr",
        }
        analysis.pipeline_params = params
        analysis.task_id = next_task_id
        analysis.error_message = None

        session.commit()
        uncommitted_output = None

        log.info(
            "Azure OCR handoff persisted",
            next_task_id=next_task_id,
            path=str(redacted_storage_path),
        )

        return {"status": "success", "analysis_id": analysis_id}

    except Exception as exc:
        session.rollback()
        log.exception("Full OCR processing failed")

        analysis = _lock_analysis(session, analysis_uuid)

        if analysis is None:
            session.rollback()
            return {"status": "error", "detail": "analysis_not_found"}

        if analysis.status in TERMINAL_STATUSES:
            result = {"status": str(analysis.status), "analysis_id": analysis_id}
            session.rollback()
            return result

        if analysis.task_id and analysis.task_id != self.request.id:
            session.rollback()
            return {"status": "ignored", "detail": "stale_task"}

        if _cancellation_requested(analysis):
            _finish_cancellation(session, analysis)
            return {"status": "cancelled", "analysis_id": analysis_id}

        analysis.error_message = str(exc)

        if self.request.retries < self.max_retries:
            # Keep the slot occupied while waiting for a retry.
            analysis.status = AnalysisStatus.OCR_IN_PROGRESS
            session.commit()
            raise self.retry(exc=exc) from exc

        analysis.status = AnalysisStatus.OCR_FAILED
        analysis.finished_at = datetime.now(UTC)

        _update_document_status(session, analysis, DocumentStatus.FAILED)
        session.commit()

        raise

    finally:
        session.close()

        if temporary_output is not None:
            temporary_output.unlink(missing_ok=True)

        if uncommitted_output is not None:
            uncommitted_output.unlink(missing_ok=True)