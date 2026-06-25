"""
Celery task: run Azure Document Intelligence OCR on a document.

After OCR succeeds, chains into the analysis task.
"""

import uuid
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
    name="app.tasks.azure_ocr.run_azure_ocr_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_azure_ocr_task(self: Task, analysis_id: str) -> dict[str, Any]:
    """
    Run Azure Document Intelligence OCR for a given DocumentAnalysis record.

    Steps:
    1. Load DocumentAnalysis + Document from DB
    2. Read file bytes from storage
    3. Call AzureOCRAdapter (Azure Document Intelligence API)
    4. Hold OCR raw result and extracted text
    5. Trigger run_analysis_task
    """
    from app.enums.analysis import AnalysisStatus

    log = logger.bind(analysis_id=analysis_id, task_id=self.request.id)
    log.info("OCR task started")
    session = _get_db_session()
    analysis = None

    try:
        from app.models.document import Document
        from app.models.document_analysis import DocumentAnalysis

        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            log.error("Analysis not found")
            return {"status": "error", "detail": "not_found"}

        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        session.commit()

        document = session.get(Document, analysis.document_id)
        if not document:
            raise ValueError(f"Document {analysis.document_id} not found in DB")

        # Read file from storage
        from pathlib import Path

        file_path = Path(document.storage_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_bytes = file_path.read_bytes()

        # Call Azure OCR
        from app.adapters.azure_ocr import AzureOCRAdapter

        adapter = AzureOCRAdapter()
        ocr_result = adapter.analyze_document(file_bytes, document.mime_type)

        # Hold results
        analysis.ocr_raw_result = ocr_result.raw
        analysis.ocr_provider = ocr_result.provider
        analysis.status = AnalysisStatus.OCR_COMPLETED
        session.commit()

        # Chain to classification + validation
        from app.tasks.analysis import run_analysis_task

        run_analysis_task.delay(analysis_id)

        log.info("OCR task completed", page_count=ocr_result.page_count)

    except Exception as exc:
        log.exception("OCR task failed")
        if analysis:
            analysis.status = AnalysisStatus.OCR_FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc) from exc

    else:
        return {"analysis_id": analysis_id, "status": "ocr_completed"}

    finally:
        session.close()
