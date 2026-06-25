"""
Celery task: classify document type and run validation after OCR.
"""

import uuid
from typing import TYPE_CHECKING, Any

import structlog
from celery import Task
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

if TYPE_CHECKING:
    from app.enums.document import DocumentType
    from app.schemas.analysis_report import ValidationIssue


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
    3. Extract MVP formal fields
    4. Load matching ValidationProfile
    5. Run RuleEngineService
    6. Persist AnalysisReport

    Args:
        analysis_id: UUID string of the DocumentAnalysis row.
    """
    from app.enums.analysis import AnalysisStatus, ValidationSeverity
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
        from app.schemas.analysis_report import ValidationIssue
        from app.services.formal_validation import FormalDocumentExtractor
        from app.services.validation import RuleEngineService

        analysis = session.get(DocumentAnalysis, uuid.UUID(analysis_id))
        if not analysis:
            return {"status": "error", "detail": "not_found"}

        analysis.status = AnalysisStatus.CLASSIFYING
        session.commit()

        document = session.get(Document, analysis.document_id)
        fallback_type = _coerce_document_type(document.document_type if document else None)
        extraction = FormalDocumentExtractor().extract(
            analysis.ocr_raw_result,
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
                log.warning("Classification fallback used", reason=str(cls_exc))

        analysis.detected_document_type = document_type.value
        analysis.classification_confidence = classification_confidence
        analysis.extracted_fields = extraction.fields
        if document:
            document.document_type = document_type
        session.commit()

        analysis.status = AnalysisStatus.VALIDATING
        session.commit()

        profile_name, rules = _load_rules_for_document_type(session, document_type)
        issues = RuleEngineService().run(rules, extraction.fields)

        if not extraction.fields.get("has_text"):
            issues.insert(
                0,
                ValidationIssue(
                    rule_name="Tekst dokumentu",
                    field_name="has_text",
                    severity=ValidationSeverity.ERROR,
                    message="Nie udało się odczytać tekstu dokumentu.",
                    details={"expected_field": "has_text"},
                ),
            )

        _upsert_report(
            session=session,
            analysis_id=analysis.id,
            issues=issues,
            total_rules=max(len(rules), 1 if not extraction.fields.get("has_text") else 0),
            summary={
                "document_type": document_type.value,
                "classification_confidence": classification_confidence,
                "profile_name": profile_name,
                "total_rules": len(rules),
                "issues": len(issues),
                "checked_fields": {
                    key: value for key, value in extraction.fields.items() if key.startswith("has_")
                },
                "detected_sections": extraction.fields.get("detected_sections", []),
            },
        )
        if document:
            document.status = DocumentStatus.PROCESSED

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


def _coerce_document_type(value: object) -> "DocumentType":
    from app.enums.document import DocumentType

    if isinstance(value, DocumentType):
        return value
    if isinstance(value, str):
        try:
            return DocumentType(value)
        except ValueError:
            return DocumentType.UNKNOWN
    return DocumentType.UNKNOWN


def _load_rules_for_document_type(
    session: Session, document_type: "DocumentType"
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
    issues: list["ValidationIssue"],
    total_rules: int,
    summary: dict[str, Any],
) -> None:
    from app.enums.analysis import ReportStatus, ValidationSeverity
    from app.models.analysis_report import AnalysisReport

    errors = [issue for issue in issues if issue.severity == ValidationSeverity.ERROR]
    warnings = [issue for issue in issues if issue.severity == ValidationSeverity.WARNING]
    infos = [issue for issue in issues if issue.severity == ValidationSeverity.INFO]

    score = 1.0 if total_rules == 0 else round(max(0.0, 1.0 - len(errors) / total_rules), 4)
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
    report.is_complete = len(errors) == 0
    report.completeness_score = score
    report.summary = summary
    session.add(report)
