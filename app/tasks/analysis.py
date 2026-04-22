"""
Celery task: classify document type and run validation after OCR.
"""

import uuid

import structlog
from celery import Task

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True, name="app.tasks.analysis.run_analysis_task", max_retries=3, default_retry_delay=30)
def run_analysis_task(self: Task, analysis_id: str) -> dict:
    """
    Classify document and run rule-based validation.

    Steps:
    1. Load analysis + OCR result
    2. Classify document type (TODO: plug in classifier)
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
        Session = sessionmaker(bind=engine)
        session = Session()

        from app.models.document_analysis import DocumentAnalysis
        from app.models.document import Document

        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            return {"status": "error", "detail": "not_found"}

        analysis.status = AnalysisStatus.CLASSIFYING
        session.commit()

        # TODO: classify document type from OCR raw result
        # detected_type = ClassifierService().classify(analysis.ocr_raw_result)
        # analysis.detected_document_type = detected_type

        # TODO: extract structured fields
        # analysis.extracted_fields = FieldExtractorService().extract(detected_type, analysis.ocr_raw_result)

        analysis.status = AnalysisStatus.VALIDATING
        session.commit()

        # TODO: load validation profile matching document type and run rule engine
        # profile = session.query(ValidationProfile).filter_by(document_type=detected_type, is_active=True).first()
        # if profile:
        #     from app.services.validation import RuleEngineService
        #     issues = RuleEngineService().run(profile.rules, analysis.extracted_fields)
        #     ... persist AnalysisReport ...

        # Update document status
        document = session.get(Document, analysis.document_id)
        if document:
            document.status = DocumentStatus.PROCESSED
            session.commit()

        analysis.status = AnalysisStatus.COMPLETED
        session.commit()

        log.info("Analysis task completed")
        return {"analysis_id": analysis_id, "status": "completed"}

    except Exception as exc:
        log.error("Analysis task failed", error=str(exc))
        if session and analysis:
            analysis.status = AnalysisStatus.FAILED
            analysis.error_message = str(exc)
            session.commit()
        raise self.retry(exc=exc)
    finally:
        if session:
            session.close()
