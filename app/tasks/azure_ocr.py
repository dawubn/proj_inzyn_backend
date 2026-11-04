"""
Celery task: run Azure Document Intelligence OCR on a document.

After OCR succeeds, persist a handoff to classification and validation.
Cancellation is checked before and after the Azure OCR call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.adapters.azure_ocr import AzureOCRAdapter
from app.enums.analysis import TERMINAL_STATUSES, AnalysisStatus, ProcessingStage
from app.enums.document import DocumentStatus
from app.models.document import Document
from app.tasks.celery_app import celery_app
from app.tasks.local_ocr import (
    _cancellation_requested,
    _finish_cancellation,
    _lock_analysis,
    _update_document_status,
)

if TYPE_CHECKING:
    from app.models.document_analysis import DocumentAnalysis

logger = structlog.get_logger(__name__)


class _AzureOCRStoppedError(Exception):
    """Stop processing without treating the current state as a task failure."""

    def __init__(self, result: dict[str, Any]) -> None:
        super().__init__(str(result.get("status", "stopped")))
        self.result = result


def _get_db_session() -> Session:
    """Create a synchronous DB session for Celery workers."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    return sessionmaker(bind=engine)()


def _azure_ocr_checkpoint(
    session: Session,
    analysis_id: uuid.UUID,
    task_id: str,
    *,
    check_dispatch: bool = False,
) -> DocumentAnalysis:
    """Lock the analysis and stop if this task must not continue."""
    analysis = _lock_analysis(session, analysis_id)

    if analysis is None:
        session.rollback()
        raise _AzureOCRStoppedError(
            {"status": "error", "detail": "analysis_not_found"}
        )

    if analysis.status in TERMINAL_STATUSES:
        result = {
            "status": str(analysis.status),
            "analysis_id": str(analysis_id),
        }
        session.rollback()
        raise _AzureOCRStoppedError(result)

    if analysis.task_id and analysis.task_id != task_id:
        session.rollback()
        raise _AzureOCRStoppedError(
            {"status": "ignored", "detail": "stale_task"}
        )

    if _cancellation_requested(analysis):
        _finish_cancellation(session, analysis)
        raise _AzureOCRStoppedError(
            {
                "status": "cancelled",
                "analysis_id": str(analysis_id),
            }
        )

    if check_dispatch and analysis.status == AnalysisStatus.QUEUED:
        session.rollback()
        raise _AzureOCRStoppedError(
            {"status": "ignored", "detail": "not_dispatched"}
        )

    return analysis


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.azure_ocr.run_azure_ocr_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_azure_ocr_task(self: Task, analysis_id: str) -> dict[str, Any]:
    """Run Azure OCR while retaining the owner's processing slot."""
    log = logger.bind(
        analysis_id=analysis_id,
        task_id=self.request.id,
    )
    log.info("Azure OCR processing started")

    analysis_uuid = uuid.UUID(analysis_id)
    task_id = str(self.request.id)
    session = _get_db_session()

    try:
        analysis = _azure_ocr_checkpoint(
            session,
            analysis_uuid,
            task_id,
            check_dispatch=True,
        )

        document = session.get(Document, analysis.document_id)
        if document is None:
            raise FileNotFoundError(
                f"Document {analysis.document_id} not found"
            )

        file_path = Path(document.storage_path)
        content_type = document.mime_type

        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        analysis.task_id = task_id
        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        analysis.processing_stage = ProcessingStage.AZURE_OCR
        analysis.processing_step = max(analysis.processing_step, 2)
        analysis.started_at = analysis.started_at or datetime.now(UTC)
        analysis.error_message = None
        document.status = DocumentStatus.PROCESSING

        # Acknowledge the persisted handoff in the same transaction
        # that marks this stage as running.
        params = dict(analysis.pipeline_params or {})
        pending_task = params.get("pending_task")
        if (
            isinstance(pending_task, dict)
            and pending_task.get("task_id") == task_id
        ):
            params.pop("pending_task", None)
            analysis.pipeline_params = params

        # Release the owner lock before reading the file and calling Azure.
        session.commit()

        file_bytes = file_path.read_bytes()
        adapter = AzureOCRAdapter()
        ocr_result = adapter.analyze_paged(file_bytes, content_type)

        # Do not overwrite a cancellation requested during the Azure call.
        analysis = _azure_ocr_checkpoint(
            session,
            analysis_uuid,
            task_id,
        )

        analysis.ocr_raw_result = ocr_result.raw
        analysis.ocr_provider = ocr_result.provider
        analysis.status = AnalysisStatus.OCR_COMPLETED
        analysis.processing_step = max(analysis.processing_step, 3)
        analysis.error_message = None

        # Keep the owner's slot occupied between OCR and validation.
        # The dispatcher publishes this handoff after the transaction commits.
        next_task_id = str(uuid.uuid4())
        params = dict(analysis.pipeline_params or {})
        params["pending_task"] = {
            "name": "app.tasks.analysis.run_analysis_task",
            "task_id": next_task_id,
            "args": [analysis_id],
            "queue": "analysis",
        }
        analysis.pipeline_params = params
        analysis.task_id = next_task_id

        session.commit()

        log.info(
            "Azure OCR completed; validation handoff persisted",
            page_count=ocr_result.page_count,
            next_task_id=next_task_id,
        )

    except _AzureOCRStoppedError as stopped:
        return stopped.result

    except Exception as exc:
        session.rollback()
        log.exception("Azure OCR processing failed")

        try:
            analysis = _azure_ocr_checkpoint(
                session,
                analysis_uuid,
                task_id,
            )
        except _AzureOCRStoppedError as stopped:
            return stopped.result

        analysis.error_message = str(exc)

        if self.request.retries < self.max_retries:
            # A retry is not a terminal failure: retain the processing slot.
            analysis.status = AnalysisStatus.OCR_IN_PROGRESS
            session.commit()
            raise self.retry(exc=exc) from exc

        analysis.status = AnalysisStatus.OCR_FAILED
        analysis.finished_at = datetime.now(UTC)

        _update_document_status(session, analysis, DocumentStatus.FAILED)
        session.commit()

        raise

    else:
        return {
            "analysis_id": analysis_id,
            "status": "ocr_completed",
        }

    finally:
        session.close()