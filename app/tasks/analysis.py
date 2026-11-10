"""
Celery task: classify document type and run validation after OCR.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from celery import Task
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.enums.analysis import TERMINAL_STATUSES, AnalysisStatus, ProcessingStage
from app.enums.document import DocumentStatus
from app.models.document import Document
from app.services.formal_report import coerce_document_type
from app.services.formal_validation import FormalDocumentExtractor
from app.tasks.celery_app import celery_app
from app.tasks.local_ocr import (
    _cancellation_requested,
    _finish_cancellation,
    _lock_analysis,
    _update_document_status,
)

if TYPE_CHECKING:
    from app.enums.document import DocumentType
    from app.models.document_analysis import DocumentAnalysis
    from app.schemas.analysis_report import ValidationIssue
    from app.services.formal_validation import FormalExtractionResult

logger = structlog.get_logger(__name__)


class _AnalysisStoppedError(Exception):
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


def _analysis_checkpoint(
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
        raise _AnalysisStoppedError({"status": "error", "detail": "analysis_not_found"})

    if analysis.status in TERMINAL_STATUSES:
        result = {
            "status": str(analysis.status),
            "analysis_id": str(analysis_id),
        }
        session.rollback()
        raise _AnalysisStoppedError(result)

    if analysis.task_id and analysis.task_id != task_id:
        session.rollback()
        raise _AnalysisStoppedError({"status": "ignored", "detail": "stale_task"})

    if _cancellation_requested(analysis):
        _finish_cancellation(session, analysis)
        raise _AnalysisStoppedError(
            {
                "status": "cancelled",
                "analysis_id": str(analysis_id),
            }
        )

    if check_dispatch and analysis.status == AnalysisStatus.QUEUED:
        session.rollback()
        raise _AnalysisStoppedError({"status": "ignored", "detail": "not_dispatched"})

    return analysis


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.analysis.run_analysis_task",
    max_retries=3,
    default_retry_delay=30,
)
def run_analysis_task(self: Task, analysis_id: str) -> dict[str, Any]:
    """Classify, validate and save results with cooperative cancellation."""
    log = logger.bind(
        analysis_id=analysis_id,
        task_id=self.request.id,
    )
    log.info("Classification and validation started")

    analysis_uuid = uuid.UUID(analysis_id)
    task_id = str(self.request.id)
    session = _get_db_session()

    try:
        analysis = _analysis_checkpoint(
            session,
            analysis_uuid,
            task_id,
            check_dispatch=True,
        )

        document = session.get(Document, analysis.document_id)
        if document is None:
            raise FileNotFoundError(f"Document {analysis.document_id} not found")

        fallback_type = coerce_document_type(document.document_type)
        ocr_raw_result = analysis.ocr_raw_result

        analysis.task_id = task_id
        analysis.status = AnalysisStatus.CLASSIFYING
        analysis.started_at = analysis.started_at or datetime.now(UTC)
        analysis.error_message = None
        document.status = DocumentStatus.PROCESSING

        params = dict(analysis.pipeline_params or {})
        pending_task = params.get("pending_task")
        if isinstance(pending_task, dict) and pending_task.get("task_id") == task_id:
            params.pop("pending_task", None)
            analysis.pipeline_params = params

        # Release the owner lock before extraction and classification.
        session.commit()

        extraction = FormalDocumentExtractor().extract(
            ocr_raw_result,
            fallback_document_type=fallback_type,
        )

        text_content = str(extraction.fields.get("document_text", "")).strip()
        document_type = extraction.document_type
        classification_confidence = extraction.confidence

        if text_content:
            from app.services.classification import ClassificationService

            classifier = ClassificationService()
            try:
                document_type, classification_confidence, _ = classifier.classify(text_content)
                log.info(
                    "Document classified",
                    document_type=document_type,
                    confidence=round(classification_confidence, 4),
                )
            except Exception as cls_exc:
                log.warning(
                    "Classification fallback used",
                    reason=str(cls_exc),
                )

        # Re-read the state before persisting classification results.
        analysis = _analysis_checkpoint(
            session,
            analysis_uuid,
            task_id,
        )

        analysis.detected_document_type = document_type.value
        analysis.classification_confidence = classification_confidence
        analysis.extracted_fields = extraction.fields
        analysis.status = AnalysisStatus.VALIDATING

        document = session.get(Document, analysis.document_id)
        if document is None:
            raise FileNotFoundError(f"Document {analysis.document_id} not found")

        document.suggested_document_type = document_type
        session.commit()

        analysis = _analysis_checkpoint(session, analysis_uuid, task_id)
        document = session.get(Document, analysis.document_id)
        _validate_and_save_report(
            session, analysis, document, document_type, classification_confidence, extraction
        )

        analysis.status = AnalysisStatus.COMPLETED
        analysis.processing_stage = ProcessingStage.COMPLETED
        analysis.processing_step = 4
        analysis.finished_at = datetime.now(UTC)
        analysis.error_message = None

        _update_document_status(session, analysis, DocumentStatus.PROCESSED)
        session.commit()

        log.info("Classification and validation completed")

    except _AnalysisStoppedError as stopped:
        return stopped.result

    except Exception as exc:
        session.rollback()
        log.exception("Classification or validation failed")

        try:
            analysis = _analysis_checkpoint(
                session,
                analysis_uuid,
                task_id,
            )
        except _AnalysisStoppedError as stopped:
            return stopped.result

        analysis.error_message = str(exc)

        if self.request.retries < self.max_retries:
            # Retain the owner's slot throughout the retry delay.
            analysis.status = AnalysisStatus.IN_PROGRESS
            session.commit()
            raise self.retry(exc=exc) from exc

        analysis.status = AnalysisStatus.FAILED
        analysis.finished_at = datetime.now(UTC)

        _update_document_status(session, analysis, DocumentStatus.FAILED)
        session.commit()

        raise

    else:
        return {"analysis_id": analysis_id, "status": "completed"}

    finally:
        session.close()


def _load_rules_for_document_type(
    session: Session,
    document_type: DocumentType,
) -> tuple[str | None, list[Any]]:
    from app.models.validation_profile import ValidationProfile
    from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition

    profile = session.execute(
        select(ValidationProfile)
        .options(selectinload(ValidationProfile.rules))
        .where(
            ValidationProfile.document_type == document_type,
            ValidationProfile.is_active.is_(True),
        )
        .order_by(ValidationProfile.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if profile:
        return profile.name, sorted(profile.rules, key=lambda rule: rule.order)

    fallback_profile = get_mvp_profile_definition(document_type)
    if not fallback_profile:
        return None, []

    return fallback_profile.name, build_rule_models(fallback_profile)


def _upsert_report(
    session: Session,
    analysis_id: uuid.UUID,
    issues: list[ValidationIssue],
    summary: dict[str, Any],
) -> None:
    from app.enums.analysis import ReportStatus, ValidationSeverity
    from app.models.analysis_report import AnalysisReport

    errors = [issue for issue in issues if issue.severity == ValidationSeverity.ERROR]
    warnings = [issue for issue in issues if issue.severity == ValidationSeverity.WARNING]
    infos = [issue for issue in issues if issue.severity == ValidationSeverity.INFO]

    score = summary.get("completeness_score")
    report = session.execute(
        select(AnalysisReport).where(AnalysisReport.analysis_id == analysis_id)
    ).scalar_one_or_none()

    if not report:
        report = AnalysisReport(analysis_id=analysis_id)

    report.status = ReportStatus.COMPLETED
    report.errors = [issue.model_dump() for issue in errors]
    report.warnings = [issue.model_dump() for issue in warnings]
    report.infos = [issue.model_dump() for issue in infos]
    report.error_count = len(errors)
    report.warning_count = len(warnings)
    report.is_complete = summary.get("is_complete")
    report.completeness_score = score
    report.summary = summary
    session.add(report)


def _validate_and_save_report(  # noqa: PLR0913
    session: Session,
    analysis: DocumentAnalysis,
    document: Document | None,
    detected_type: DocumentType,
    confidence: float,
    extraction: FormalExtractionResult | None = None,
) -> None:
    from app.services.formal_issue_locator import locate_formal_issues
    from app.services.formal_report import FormalValidationService, resolve_document_type
    from app.services.formal_validation import FormalDocumentExtractor

    effective, type_summary = resolve_document_type(
        document.document_type if document else None, detected_type, confidence
    )
    if extraction is None or extraction.validation_document_type != effective:
        extraction = FormalDocumentExtractor().extract(analysis.ocr_raw_result, effective)
    analysis.extracted_fields = extraction.fields
    profile_name, rules = _load_rules_for_document_type(session, effective)
    issues, summary = FormalValidationService().build_report(
        rules, extraction.fields, profile_name, effective
    )
    issues = locate_formal_issues(issues, analysis.tesseract_words or [])
    summary.update(type_summary)
    _upsert_report(session, analysis.id, issues, summary)
