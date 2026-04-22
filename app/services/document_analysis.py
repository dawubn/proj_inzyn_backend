import uuid

import structlog

from app.core.exceptions import NotFoundError
from app.enums.analysis import AnalysisStatus
from app.enums.document import DocumentStatus
from app.models.document_analysis import DocumentAnalysis
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.tasks.ocr import run_ocr_task

logger = structlog.get_logger(__name__)


class DocumentAnalysisService:
    def __init__(
        self,
        analysis_repo: DocumentAnalysisRepository,
        document_repo: DocumentRepository,
    ) -> None:
        self._analyses = analysis_repo
        self._docs = document_repo

    async def trigger_analysis(self, document_id: uuid.UUID, owner_id: uuid.UUID) -> DocumentAnalysis:
        doc = await self._docs.get_by_id(document_id)
        if not doc or doc.owner_id != owner_id:
            raise NotFoundError("Document not found")

        analysis = DocumentAnalysis(
            document_id=document_id,
            status=AnalysisStatus.PENDING,
        )
        analysis = await self._analyses.create(analysis)

        # Dispatch async Celery task
        task = run_ocr_task.delay(str(analysis.id))
        analysis.task_id = task.id

        # Update document status
        await self._docs.update_status(doc, DocumentStatus.QUEUED)

        logger.info("Analysis triggered", analysis_id=str(analysis.id), task_id=task.id)
        return analysis

    async def get_or_raise(self, analysis_id: uuid.UUID) -> DocumentAnalysis:
        analysis = await self._analyses.get_by_id(analysis_id)
        if not analysis:
            raise NotFoundError("Analysis not found")
        return analysis
