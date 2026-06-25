import uuid

import structlog

from app.core.exceptions import NotFoundError
from app.enums.analysis import AnalysisStatus
from app.enums.document import DocumentStatus
from app.models.document_analysis import DocumentAnalysis
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository
from app.tasks.azure_ocr import run_azure_ocr_task

logger = structlog.get_logger(__name__)


class DocumentAnalysisService:
    def __init__(
        self,
        analysis_repo: DocumentAnalysisRepository,
        document_repo: DocumentRepository,
    ) -> None:
        self._analyses = analysis_repo
        self._docs = document_repo

    async def trigger_azure_ocr(
        self, document_id: uuid.UUID, owner_id: uuid.UUID
    ) -> DocumentAnalysis:
        doc = await self._docs.get_by_id(document_id)
        if not doc or doc.owner_id != owner_id:
            raise NotFoundError("Document not found")

        analysis = DocumentAnalysis(
            document_id=document_id,
            status=AnalysisStatus.PENDING,
        )
        analysis = await self._analyses.create(analysis)

        # Dispatch async Celery task
        task = run_azure_ocr_task.delay(str(analysis.id))
        analysis.task_id = task.id

        # Update document status
        await self._docs.update_status(doc, DocumentStatus.QUEUED)

        logger.info("AzureOCR analysis triggered", analysis_id=str(analysis.id), task_id=task.id)
        return analysis

    async def trigger_analysis_with_redaction(
        self, document_id: uuid.UUID, owner_id: uuid.UUID
    ) -> DocumentAnalysis:
        """Trigger analysis with complete OCR pipeline (local OCR + redaction + Azure OCR)."""
        from app.tasks.full_ocr import run_full_ocr_task

        doc = await self._docs.get_by_id(document_id)
        if not doc or doc.owner_id != owner_id:
            raise NotFoundError("Document not found")

        analysis = DocumentAnalysis(
            document_id=document_id,
            status=AnalysisStatus.PENDING,
        )
        analysis = await self._analyses.create(analysis)

        # Dispatch async Celery task (full pipeline)
        task = run_full_ocr_task.delay(str(analysis.id), str(document_id))
        analysis.task_id = task.id
        analysis = await self._analyses.update(analysis)

        # Ensure commit before task starts (avoid race condition)
        await self._analyses.session.commit()

        # Update document status
        await self._docs.update_status(doc, DocumentStatus.QUEUED)

        logger.info(
            "Analysis with redaction triggered", analysis_id=str(analysis.id), task_id=task.id
        )
        return analysis

    async def trigger_legal_analysis(
        self, document_id: uuid.UUID, owner_id: uuid.UUID
    ) -> DocumentAnalysis:
        """Trigger legal analysis: full OCR pipeline + legal analysis via Azure LLM."""
        from app.tasks.legal_analysis import run_legal_analysis_task

        doc = await self._docs.get_by_id(document_id)
        if not doc or doc.owner_id != owner_id:
            raise NotFoundError("Document not found")

        analysis = DocumentAnalysis(
            document_id=document_id,
            status=AnalysisStatus.PENDING,
        )
        analysis = await self._analyses.create(analysis)

        # Dispatch async Celery task (full pipeline + legal analysis)
        task = run_legal_analysis_task.delay(str(analysis.id), str(document_id))
        analysis.task_id = task.id
        analysis = await self._analyses.update(analysis)

        # Ensure commit before task starts (avoid race condition)
        await self._analyses.session.commit()

        # Update document status
        await self._docs.update_status(doc, DocumentStatus.QUEUED)

        logger.info("Legal analysis triggered", analysis_id=str(analysis.id), task_id=task.id)
        return analysis

    async def get_or_raise(
        self, analysis_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> DocumentAnalysis:
        analysis = await self._analyses.get_by_id(analysis_id)
        if not analysis:
            raise NotFoundError("Analysis not found")
        if owner_id:
            doc = await self._docs.get_by_id(analysis.document_id)
            if not doc or doc.owner_id != owner_id:
                raise NotFoundError("Analysis not found")
        return analysis
