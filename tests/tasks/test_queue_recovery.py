import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.enums.analysis import AnalysisStatus
from app.enums.document import DocumentStatus
from app.models.document import Document
from app.models.document_analysis import DocumentAnalysis
from app.models.user import User
from app.tasks import queue_dispatcher as dispatcher
from app.tasks.celery_app import celery_app


@pytest.mark.asyncio
async def test_five_documents_continue_after_worker_loss(create_tables, monkeypatch, tmp_path):
    url = os.environ.get(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://cerberdoc:cerberdoc@localhost:5432/cerber_doc_test",
    ).replace("+asyncpg", "+psycopg2")
    engine = create_engine(url)
    states: dict[str, str] = {}
    monkeypatch.setattr(
        celery_app,
        "AsyncResult",
        lambda task_id: SimpleNamespace(state=states.get(task_id, "STARTED")),
    )
    published = MagicMock()
    monkeypatch.setattr(celery_app, "send_task", published)
    with Session(engine) as session:
        owner = User(
            email=f"{uuid.uuid4()}@example.com", hashed_password=uuid.uuid4().hex, full_name="Test"
        )
        session.add(owner)
        session.flush()
        analyses = []
        for index in range(5):
            document = Document(
                owner_id=owner.id,
                original_filename=f"{index}.pdf",
                storage_path=str(tmp_path / "test.pdf"),
                file_extension="pdf",
                file_size_bytes=10,
                mime_type="application/pdf",
            )
            session.add(document)
            session.flush()
            analysis = DocumentAnalysis(
                document_id=document.id,
                owner_id=owner.id,
                status=AnalysisStatus.QUEUED,
                pipeline="legal_analysis",
                queued_at=datetime.now(UTC) + timedelta(seconds=index),
            )
            session.add(analysis)
            analyses.append(analysis)
        session.commit()
        for index in range(3):
            assert dispatcher._reserve_next(session, owner.id)
            assert dispatcher._publish_pending(session, owner.id)
            assert analyses[index].status == AnalysisStatus.IN_PROGRESS
            assert not dispatcher._reserve_next(session, owner.id)
            analyses[index].status = AnalysisStatus.COMPLETED
            session.commit()
        assert dispatcher._reserve_next(session, owner.id)
        assert dispatcher._publish_pending(session, owner.id)
        fourth = analyses[3]
        assert fourth.task_id is not None
        states[fourth.task_id] = "FAILURE"
        assert dispatcher._reserve_next(session, owner.id)
        assert fourth.status == AnalysisStatus.FAILED
        assert fourth.finished_at is not None
        failed_document = session.get(Document, fourth.document_id)
        assert failed_document is not None
        assert failed_document.status == DocumentStatus.FAILED
        assert analyses[4].status == AnalysisStatus.IN_PROGRESS
        assert dispatcher._publish_pending(session, owner.id)
        assert published.call_count == 5
        assert not dispatcher._reserve_next(session, owner.id)
        analyses[4].status = AnalysisStatus.COMPLETED
        session.commit()
        assert not dispatcher._reserve_next(session, owner.id)
        session.delete(owner)
        session.commit()
    engine.dispose()


@pytest.mark.parametrize("state", ["PENDING", "STARTED", "RETRY", "SUCCESS"])
def test_non_failure_result_never_releases_active_slot(monkeypatch, state):
    monkeypatch.setattr(celery_app, "AsyncResult", lambda _: SimpleNamespace(state=state))
    session = MagicMock()
    analysis = DocumentAnalysis(task_id="current", status=AnalysisStatus.IN_PROGRESS)
    assert not dispatcher._recover_failed_task(session, analysis)
    assert analysis.status == AnalysisStatus.IN_PROGRESS
    session.flush.assert_not_called()
