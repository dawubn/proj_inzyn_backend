import uuid
from pathlib import Path
from typing import Any

import structlog
from celery import Task
from sqlalchemy.orm import Session

from app.adapters.local_ocr import LocalOCRAdapter
from app.enums.analysis import AnalysisStatus
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
    name="app.tasks.local_ocr.run_local_ocr_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_local_ocr_task(self: Task, analysis_id: str, document_id: str) -> dict[str, Any]:
    """
    Run local OCR (Tesseract) on a document.

    Steps:
    1. Load analysis and document from DB
    2. Set status to OCR_IN_PROGRESS
    3. Load file from disk
    4. Run LocalOCRAdapter
    5. Save result to DB
    """
    log = logger.bind(analysis_id=analysis_id, document_id=document_id, task_id=self.request.id)
    log.info("Local OCR task started")

    session = _get_db_session()
    analysis = None

    try:
        from app.models.document import Document
        from app.models.document_analysis import DocumentAnalysis

        # 1. Load analysis and document from DB
        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            log.error("Analysis not found")
            return {"status": "error", "detail": "analysis_not_found"}

        # 2. Set status to OCR_IN_PROGRESS
        analysis.status = AnalysisStatus.OCR_IN_PROGRESS
        session.commit()
        log.info("Status updated to OCR_IN_PROGRESS")

        # Załaduj dokument
        document = session.get(Document, uuid.UUID(document_id))
        if not document:
            raise FileNotFoundError(f"Document {document_id} not found")

        # 3. Load file from disk
        file_path = Path(document.storage_path)
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        # 4. Run LocalOCRAdapter
        ocr_adapter = LocalOCRAdapter()

        if document.file_extension.lower() == "pdf":
            words_per_page, images = ocr_adapter.ocr_pdf(file_path)
        else:
            words_per_page, images = ocr_adapter.ocr_image(file_path)

        log.info("Local OCR completed", pages=len(words_per_page))

        # 5. Save result to DB
        analysis.ocr_raw_result = {
            "words_per_page": words_per_page,
            "page_count": len(words_per_page),
            "provider": "local_tesseract",
        }
        analysis.ocr_provider = "local_tesseract"
        analysis.status = AnalysisStatus.OCR_COMPLETED
        session.commit()
        log.info("Local OCR result saved", pages=len(words_per_page))

    except Exception as exc:
        log.exception("Local OCR task failed")
        if analysis:
            analysis.status = AnalysisStatus.OCR_FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc) from exc

    else:
        return {"status": "success", "analysis_id": analysis_id}

    finally:
        session.close()
