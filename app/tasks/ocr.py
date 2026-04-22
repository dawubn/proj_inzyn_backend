"""
Celery task: run OCR on a document and persist the result.

After OCR succeeds, chains into the analysis task.
"""

import asyncio
import uuid

import structlog
from celery import Task

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


def _get_db_session():  # type: ignore[return]
    """Helper to create a synchronous DB session for Celery workers."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)
    return sessionmaker(bind=engine)()


@celery_app.task(bind=True, name="app.tasks.ocr.run_ocr_task", max_retries=3, default_retry_delay=30)
def run_ocr_task(self: Task, analysis_id: str) -> dict:
    """
    Run OCR for a given DocumentAnalysis record.

    Steps:
    1. Load analysis + document from DB
    2. Read file from storage
    3. Call Azure OCR adapter
    4. Persist OCR raw result
    5. Trigger next task: classify + validate

    Args:
        analysis_id: UUID string of the DocumentAnalysis row.

    Returns:
        dict with analysis_id and status.
    """
    from app.enums.analysis import AnalysisStatus

    log = logger.bind(analysis_id=analysis_id, task_id=self.request.id)
    log.info("OCR task started")

    session = _get_db_session()
    try:
        from app.models.document_analysis import DocumentAnalysis
        from app.models.document import Document

        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            log.error("Analysis not found")
            return {"status": "error", "detail": "not_found"}

        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        session.commit()

        document = session.get(Document, analysis.document_id)
        if not document:
            raise ValueError("Document missing")

        # TODO: read file from storage and call OCR adapter
        # from app.adapters.azure_ocr import AzureOCRAdapter
        # adapter = AzureOCRAdapter()
        # with open(document.storage_path, "rb") as f:
        #     file_bytes = f.read()
        # ocr_result = adapter.analyze_document(file_bytes, document.mime_type)
        # analysis.ocr_raw_result = ocr_result.raw
        # analysis.ocr_provider = "azure"

        analysis.status = AnalysisStatus.OCR_COMPLETED
        session.commit()

        # Chain to classification + validation task
        run_analysis_task.delay(analysis_id)

        log.info("OCR task completed")
        return {"analysis_id": analysis_id, "status": "ocr_completed"}

    except Exception as exc:
        log.error("OCR task failed", error=str(exc))
        if analysis:
            analysis.status = AnalysisStatus.OCR_FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc)
    finally:
        session.close()


from app.tasks.analysis import run_analysis_task  # noqa: E402 — circular-safe late import
