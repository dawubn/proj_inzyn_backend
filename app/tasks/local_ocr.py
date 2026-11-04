import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from celery import Task
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.adapters.local_ocr import LocalOCRAdapter
from app.enums.analysis import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    AnalysisStatus,
    ProcessingStage,
)
from app.enums.document import DocumentStatus
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
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


def _lock_analysis(
    session: Session,
    analysis_id: uuid.UUID,
) -> DocumentAnalysis | None:
    """Use the same owner lock as the API and refresh the processing state."""
    owner_id = session.execute(
        select(DocumentAnalysis.owner_id).where(DocumentAnalysis.id == analysis_id)
    ).scalar_one_or_none()

    if owner_id is None:
        return None

    session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(CAST(:owner_id AS text), 0)"
            ")"
        ),
        {"owner_id": str(owner_id)},
    )

    return session.execute(
        select(DocumentAnalysis)
        .where(DocumentAnalysis.id == analysis_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _update_document_status(
    session: Session,
    analysis: DocumentAnalysis,
    fallback: DocumentStatus,
) -> None:
    """Do not hide other processing requests for the same document."""
    document = session.get(Document, analysis.document_id)
    if document is None:
        return

    other_statuses = list(
        session.execute(
            select(DocumentAnalysis.status).where(
                DocumentAnalysis.document_id == analysis.document_id,
                DocumentAnalysis.id != analysis.id,
            )
        ).scalars()
    )

    if any(status in ACTIVE_STATUSES for status in other_statuses):
        document.status = DocumentStatus.PROCESSING
    elif any(
        status in {AnalysisStatus.QUEUED, AnalysisStatus.PENDING}
        for status in other_statuses
    ):
        document.status = DocumentStatus.QUEUED
    elif (
        fallback == DocumentStatus.UPLOADED
        and AnalysisStatus.COMPLETED in other_statuses
    ):
        document.status = DocumentStatus.PROCESSED
    else:
        document.status = fallback


def _finish_cancellation(
    session: Session,
    analysis: DocumentAnalysis,
) -> None:
    """Confirm cancellation only after this worker has stopped OCR work."""
    now = datetime.now(UTC)

    analysis.cancel_requested_at = analysis.cancel_requested_at or now
    analysis.cancelled_at = now
    analysis.finished_at = now
    analysis.status = AnalysisStatus.CANCELLED

    _update_document_status(session, analysis, DocumentStatus.UPLOADED)
    session.commit()


def _cancellation_requested(analysis: DocumentAnalysis) -> bool:
    return (
        analysis.status == AnalysisStatus.CANCELLING
        or analysis.cancel_requested_at is not None
    )


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.local_ocr.run_local_ocr_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_local_ocr_task(
    self: Task,
    analysis_id: str,
    document_id: str,
) -> dict[str, Any]:
    """Run standalone local OCR with cooperative cancellation."""
    log = logger.bind(
        analysis_id=analysis_id,
        document_id=document_id,
        task_id=self.request.id,
    )
    log.info("Local OCR processing started")

    analysis_uuid = uuid.UUID(analysis_id)
    document_uuid = uuid.UUID(document_id)
    session = _get_db_session()

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

        # Only the dispatcher can reserve a queued request for execution.
        if analysis.status == AnalysisStatus.QUEUED:
            session.rollback()
            return {"status": "ignored", "detail": "not_dispatched"}

        document = session.get(Document, document_uuid)
        if document is None:
            raise FileNotFoundError(f"Document {document_id} not found")

        file_path = Path(document.storage_path)
        file_extension = document.file_extension.lower()

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        analysis.task_id = self.request.id
        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        analysis.processing_stage = ProcessingStage.LOCAL_OCR
        analysis.started_at = analysis.started_at or datetime.now(UTC)
        analysis.error_message = None
        document.status = DocumentStatus.PROCESSING

        # Release the owner lock before the expensive OCR call.
        session.commit()

        ocr_adapter = LocalOCRAdapter()

        if file_extension == "pdf":
            words_per_page, _images = ocr_adapter.ocr_pdf(file_path)
        else:
            words_per_page, _images = ocr_adapter.ocr_image(file_path)

        # Re-read under the owner lock before writing any results.
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

        analysis.ocr_raw_result = {
            "words_per_page": words_per_page,
            "page_count": len(words_per_page),
            "provider": "local_tesseract",
        }
        analysis.ocr_provider = "local_tesseract"

        # Standalone local OCR ends here; OCR_COMPLETED would retain the slot.
        analysis.status = AnalysisStatus.COMPLETED
        analysis.processing_stage = ProcessingStage.COMPLETED
        analysis.processing_step = 4
        analysis.finished_at = datetime.now(UTC)
        analysis.error_message = None

        _update_document_status(session, analysis, DocumentStatus.PROCESSED)
        session.commit()

        log.info("Local OCR processing completed", pages=len(words_per_page))

        return {"status": "success", "analysis_id": analysis_id}

    except Exception as exc:
        session.rollback()
        log.exception("Local OCR processing failed")

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
            # Retain the owner's slot during the retry delay.
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