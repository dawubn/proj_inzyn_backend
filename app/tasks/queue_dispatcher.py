"""
Dispatch per-user processing queues and persisted pipeline handoffs.

The owner advisory lock is shared with the API and processing tasks.
Only one processing request per owner can occupy an active slot.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from celery import Task
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.enums.analysis import ACTIVE_STATUSES, AnalysisStatus
from app.enums.document import DocumentStatus
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

PIPELINE_TASKS: dict[str, tuple[str, str, bool]] = {
    "local_ocr": (
        "app.tasks.local_ocr.run_local_ocr_task",
        "ocr",
        True,
    ),
    "full_ocr": (
        "app.tasks.full_ocr.run_full_ocr_task",
        "ocr",
        True,
    ),
    "azure_ocr": (
        "app.tasks.azure_ocr.run_azure_ocr_task",
        "ocr",
        False,
    ),
    "legal_analysis": (
        "app.tasks.legal_analysis.run_legal_analysis_task",
        "ocr",
        True,
    ),
}

ALLOWED_TASK_QUEUES: dict[str, str] = {
    task_name: queue
    for task_name, queue, _needs_document_id in PIPELINE_TASKS.values()
}
ALLOWED_TASK_QUEUES["app.tasks.analysis.run_analysis_task"] = "analysis"


def _get_db_session() -> Session:
    """Create a synchronous DB session for the dispatcher."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import settings

    sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    engine = create_engine(sync_url)

    return sessionmaker(bind=engine)()


def _lock_owner(session: Session, owner_id: uuid.UUID) -> None:
    """Hold the shared per-owner lock until commit or rollback."""
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended(CAST(:owner_id AS text), 0)"
            ")"
        ),
        {"owner_id": str(owner_id)},
    )


def _get_active(
    session: Session,
    owner_id: uuid.UUID,
) -> DocumentAnalysis | None:
    return session.execute(
        select(DocumentAnalysis)
        .where(
            DocumentAnalysis.owner_id == owner_id,
            DocumentAnalysis.status.in_(ACTIVE_STATUSES),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _get_next_queued(
    session: Session,
    owner_id: uuid.UUID,
) -> DocumentAnalysis | None:
    return session.execute(
        select(DocumentAnalysis)
        .where(
            DocumentAnalysis.owner_id == owner_id,
            DocumentAnalysis.status == AnalysisStatus.QUEUED,
        )
        .order_by(
            DocumentAnalysis.queued_at.asc().nulls_last(),
            DocumentAnalysis.id.asc(),
        )
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def _reserve_next(
    session: Session,
    owner_id: uuid.UUID,
) -> bool:
    """Reserve an owner's slot and persist the initial task before publication."""
    _lock_owner(session, owner_id)

    if _get_active(session, owner_id) is not None:
        session.commit()
        return False

    analysis = _get_next_queued(session, owner_id)
    if analysis is None:
        session.commit()
        return False

    pipeline = analysis.pipeline
    if pipeline not in PIPELINE_TASKS:
        analysis.status = AnalysisStatus.FAILED
        analysis.finished_at = datetime.now(UTC)
        analysis.error_message = f"Unsupported processing pipeline: {pipeline}"
        session.commit()

        logger.error(
            "Cannot dispatch unsupported pipeline",
            analysis_id=str(analysis.id),
            owner_id=str(owner_id),
            pipeline=pipeline,
        )
        return False

    document = session.get(Document, analysis.document_id)
    if document is None:
        analysis.status = AnalysisStatus.FAILED
        analysis.finished_at = datetime.now(UTC)
        analysis.error_message = "Document not found"
        session.commit()
        return False

    if document.owner_id != owner_id:
        analysis.status = AnalysisStatus.FAILED
        analysis.finished_at = datetime.now(UTC)
        analysis.error_message = "Document owner does not match processing owner"
        session.commit()
        return False

    task_name, queue, needs_document_id = PIPELINE_TASKS[pipeline]
    task_id = str(uuid.uuid4())

    args = [str(analysis.id)]
    if needs_document_id:
        args.append(str(analysis.document_id))

    params = dict(analysis.pipeline_params or {})
    params["pending_task"] = {
        "name": task_name,
        "task_id": task_id,
        "args": args,
        "queue": queue,
    }

    analysis.pipeline_params = params
    analysis.task_id = task_id
    analysis.status = AnalysisStatus.IN_PROGRESS
    analysis.error_message = None

    document.status = DocumentStatus.PROCESSING

    analysis_id = str(analysis.id)

    # Commit the reservation before publishing anything to the broker.
    session.commit()

    logger.info(
        "Processing slot reserved",
        analysis_id=analysis_id,
        owner_id=str(owner_id),
        task_id=task_id,
        task_name=task_name,
    )

    return True


def _validate_pending_task(
    analysis: DocumentAnalysis,
    pending_task: dict[str, Any],
) -> tuple[str, str, list[str], str]:
    """Accept only known task names, queues and arguments for this analysis."""
    task_name = pending_task.get("name")
    task_id = pending_task.get("task_id")
    args = pending_task.get("args")
    queue = pending_task.get("queue")

    if not isinstance(task_name, str):
        raise TypeError("Pending task name must be a string")

    expected_queue = ALLOWED_TASK_QUEUES.get(task_name)
    if expected_queue is None or queue != expected_queue:
        raise ValueError("Unknown task or invalid processing queue")

    if not isinstance(task_id, str) or not task_id:
        raise ValueError("Pending task ID is missing")

    if analysis.task_id != task_id:
        raise ValueError("Pending task ID differs from the current task ID")

    if not isinstance(args, list) or not all(
        isinstance(value, str) for value in args
    ):
        raise ValueError("Pending task arguments must be a list of strings")

    expected_args = [str(analysis.id)]

    if task_name in {
        "app.tasks.local_ocr.run_local_ocr_task",
        "app.tasks.full_ocr.run_full_ocr_task",
        "app.tasks.legal_analysis.run_legal_analysis_task",
    }:
        expected_args.append(str(analysis.document_id))

    if args != expected_args:
        raise ValueError("Pending task arguments do not match the processing request")

    return task_name, task_id, args, expected_queue


def _publish_pending(
    session: Session,
    owner_id: uuid.UUID,
) -> bool:
    """Publish an unpublished initial task or a persisted pipeline handoff."""
    _lock_owner(session, owner_id)

    analysis = _get_active(session, owner_id)
    if analysis is None:
        session.commit()
        return False

    params = dict(analysis.pipeline_params or {})
    pending_task = params.get("pending_task")

    if not isinstance(pending_task, dict):
        session.commit()
        return False

    # Do not repeatedly publish a task already accepted by the broker.
    if pending_task.get("published_at"):
        session.commit()
        return False

    task_name, task_id, args, queue = _validate_pending_task(
        analysis,
        pending_task,
    )

    # For CANCELLING, still deliver the task. Its first checkpoint confirms
    # cancellation without entering OCR or LLM processing.
    #
    # Do not release the slot here: an earlier publication attempt may have
    # reached the broker even if its database transaction did not commit.
    celery_app.send_task(
        task_name,
        args=args,
        task_id=task_id,
        queue=queue,
    )

    params["pending_task"] = {
        **pending_task,
        "published_at": datetime.now(UTC).isoformat(),
    }
    analysis.pipeline_params = params

    analysis_id = str(analysis.id)
    session.commit()

    logger.info(
        "Processing task published",
        analysis_id=analysis_id,
        owner_id=str(owner_id),
        task_id=task_id,
        task_name=task_name,
        queue=queue,
    )

    return True


@celery_app.task(  # type: ignore[misc]
    bind=True,
    name="app.tasks.queue_dispatcher.dispatch_processing_queues",
    ignore_result=True,
)
def dispatch_processing_queues(self: Task) -> dict[str, int]:
    """Reserve free owner slots and publish outstanding processing tasks."""
    log = logger.bind(task_id=self.request.id)
    session = _get_db_session()

    reserved_count = 0
    published_count = 0
    error_count = 0

    try:
        relevant_statuses = [
            AnalysisStatus.QUEUED,
            *ACTIVE_STATUSES,
        ]

        owners = list(
            session.execute(
                select(DocumentAnalysis.owner_id)
                .where(DocumentAnalysis.status.in_(relevant_statuses))
                .distinct()
                .order_by(DocumentAnalysis.owner_id.asc())
            ).scalars()
        )

        session.commit()

        for owner_id in owners:
            try:
                if _reserve_next(session, owner_id):
                    reserved_count += 1

                if _publish_pending(session, owner_id):
                    published_count += 1

            except Exception:
                session.rollback()
                error_count += 1
                log.exception(
                    "Processing queue dispatch failed",
                    owner_id=str(owner_id),
                )

        return {
            "owners": len(owners),
            "reserved": reserved_count,
            "published": published_count,
            "errors": error_count,
        }

    finally:
        session.close()