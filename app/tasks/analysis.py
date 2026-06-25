"""
Celery task: classify document type and run validation after OCR.
"""

import uuid
from typing import Any

import structlog
from celery import Task

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.analysis.run_analysis_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_analysis_task(self: Task, analysis_id: str) -> dict[str, Any]:
    """
    Classify document and run rule-based validation.

    Steps:
    1. Load analysis + OCR result
    2. Classify document type using TF-IDF + Logistic Regression
    3. Extract fields (TODO: field extractor)
    4. Load matching ValidationProfile
    5. Run RuleEngineService
    6. Persist AnalysisReport

    Args:
        analysis_id: UUID string of the DocumentAnalysis row.
    """
    from app.enums.analysis import AnalysisStatus
    from app.enums.document import DocumentStatus

    log = logger.bind(analysis_id=analysis_id, task_id=self.request.id)
    log.info("Analysis task started")

    session = None
    analysis = None
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        from app.core.config import settings

        sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)
        session_factory = sessionmaker(bind=engine)
        session = session_factory()

        from app.models.document import Document
        from app.models.document_analysis import DocumentAnalysis

        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            return {"status": "error", "detail": "not_found"}

        analysis.status = AnalysisStatus.CLASSIFYING
        session.commit()

        document = session.get(Document, analysis.document_id)

        text_content = (
            str(analysis.ocr_raw_result.get("content", "")) if analysis.ocr_raw_result else ""
        )
        if text_content:
            from app.services.classification import ClassificationService

            classifier = ClassificationService()
            try:
                doc_type, confidence, _ = classifier.classify(text_content)
                analysis.detected_document_type = doc_type.value
                analysis.classification_confidence = confidence
                if document:
                    document.document_type = doc_type
                session.commit()
                log.info(
                    "Document classified",
                    document_type=doc_type,
                    confidence=round(confidence, 4),
                )
            except Exception as cls_exc:
                log.warning("Classification skipped", reason=str(cls_exc))

        analysis.status = AnalysisStatus.VALIDATING
        session.commit()

        if document:
            document.status = DocumentStatus.PROCESSED
            session.commit()

        analysis.status = AnalysisStatus.COMPLETED
        session.commit()

        log.info("Analysis task completed")
    except Exception as exc:
        log.exception("Analysis task failed")
        if session and analysis:
            analysis.status = AnalysisStatus.FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc) from exc
    else:
        return {"analysis_id": analysis_id, "status": "completed"}
    finally:
        if session:
            session.close()
