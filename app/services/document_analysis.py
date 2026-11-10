import uuid
from datetime import UTC, datetime

import structlog

from app.core.exceptions import ConflictError, NotFoundError
from app.enums.analysis import ACTIVE_STATUSES, TERMINAL_STATUSES, AnalysisStatus
from app.enums.document import DocumentStatus
from app.models.document_analysis import DocumentAnalysis
from app.repositories.document import DocumentRepository
from app.repositories.document_analysis import DocumentAnalysisRepository

logger = structlog.get_logger(__name__)

SUPPORTED_PIPELINES: frozenset[str] = frozenset(
    {
        "local_ocr",
        "azure_ocr",
        "full_ocr",
        "legal_analysis",
    }
)


class DocumentAnalysisService:
    def __init__(
        self,
        analysis_repo: DocumentAnalysisRepository,
        document_repo: DocumentRepository,
    ) -> None:
        self._analyses = analysis_repo
        self._docs = document_repo

    async def enqueue(
        self,
        document_id: uuid.UUID,
        owner_id: uuid.UUID,
        pipeline: str,
    ) -> DocumentAnalysis:
        """Persist a waiting processing request without publishing a Celery task."""
        if pipeline not in SUPPORTED_PIPELINES:
            raise ValueError(f"Unsupported processing pipeline: {pipeline}")

        await self._analyses.lock_owner(owner_id)

        doc = await self._docs.get_by_id(document_id)
        if not doc or doc.owner_id != owner_id:
            raise NotFoundError("Document not found")

        analysis = DocumentAnalysis(
            document_id=document_id,
            owner_id=owner_id,
            status=AnalysisStatus.QUEUED,
            pipeline=pipeline,
            pipeline_params={},
            queued_at=datetime.now(UTC),
        )
        analysis = await self._analyses.create(analysis)

        # An already running request for the same document must retain
        # its document-level processing status.
        document_analyses = await self._analyses.get_by_document(document_id)
        has_active_processing = any(item.status in ACTIVE_STATUSES for item in document_analyses)
        if not has_active_processing:
            await self._docs.update_status(doc, DocumentStatus.QUEUED)

        logger.info(
            "Processing queued",
            analysis_id=str(analysis.id),
            document_id=str(document_id),
            owner_id=str(owner_id),
            pipeline=pipeline,
        )

        return analysis

    async def trigger_local_ocr(
        self,
        document_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> DocumentAnalysis:
        return await self.enqueue(document_id, owner_id, "local_ocr")

    async def trigger_azure_ocr(
        self,
        document_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> DocumentAnalysis:
        return await self.enqueue(document_id, owner_id, "azure_ocr")

    async def trigger_analysis_with_redaction(
        self,
        document_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> DocumentAnalysis:
        return await self.enqueue(document_id, owner_id, "full_ocr")

    async def trigger_legal_analysis(
        self,
        document_id: uuid.UUID,
        owner_id: uuid.UUID,
    ) -> DocumentAnalysis:
        return await self.enqueue(document_id, owner_id, "legal_analysis")

    async def get_or_raise(
        self,
        analysis_id: uuid.UUID,
        owner_id: uuid.UUID | None = None,
    ) -> DocumentAnalysis:
        analysis = await self._analyses.get_by_id(analysis_id)
        if not analysis:
            raise NotFoundError("Analysis not found")

        if owner_id is not None:
            doc = await self._docs.get_by_id(analysis.document_id)
            if not doc or doc.owner_id != owner_id:
                raise NotFoundError("Analysis not found")

        return analysis

    async def get_queue_position(
        self,
        analysis: DocumentAnalysis,
    ) -> int | None:
        return await self._analyses.get_queue_position(analysis)

    async def cancel_processing(
        self,
        analysis_id: uuid.UUID,
        owner_id: uuid.UUID | None = None,
    ) -> DocumentAnalysis:
        """
        Cancel a waiting request immediately.

        Running processing transitions to CANCELLING and retains its slot
        until a worker confirms that processing has stopped.
        """
        analysis = await self.get_or_raise(analysis_id, owner_id)

        await self._analyses.lock_owner(analysis.owner_id)

        locked_analysis = await self._analyses.get_by_id_for_update(analysis_id)
        if not locked_analysis:
            raise NotFoundError("Analysis not found")

        analysis = locked_analysis

        if owner_id is not None and analysis.owner_id != owner_id:
            raise NotFoundError("Analysis not found")

        # Repeated cancellation requests are safe.
        if analysis.status in {
            AnalysisStatus.CANCELLING,
            AnalysisStatus.CANCELLED,
        }:
            return analysis

        if analysis.status in TERMINAL_STATUSES:
            raise ConflictError("Processing has already finished.")

        now = datetime.now(UTC)

        if analysis.status == AnalysisStatus.QUEUED:
            analysis.cancel_requested_at = now
            analysis.cancelled_at = now
            analysis.finished_at = now
            analysis.status = AnalysisStatus.CANCELLED

            await self._analyses.update(analysis)
            await self._sync_document_status_after_cancel(analysis)

        elif analysis.status in ACTIVE_STATUSES or analysis.status == AnalysisStatus.PENDING:
            # PENDING may represent a legacy task already published to Celery.
            # Do not assume it is safe to release the owner's slot.
            analysis.cancel_requested_at = now
            analysis.status = AnalysisStatus.CANCELLING

            await self._analyses.update(analysis)

        else:
            raise ConflictError("Processing cannot be cancelled in its current state.")

        logger.info(
            "Processing cancellation requested",
            analysis_id=str(analysis.id),
            owner_id=str(analysis.owner_id),
            processing_status=analysis.status,
        )

        return analysis

    async def _sync_document_status_after_cancel(
        self,
        analysis: DocumentAnalysis,
    ) -> None:
        """Preserve document status when other requests still need the document."""
        doc = await self._docs.get_by_id(analysis.document_id)
        if not doc:
            return

        document_analyses = await self._analyses.get_by_document(analysis.document_id)

        other_analyses = [item for item in document_analyses if item.id != analysis.id]

        if any(item.status in ACTIVE_STATUSES for item in other_analyses):
            return

        if any(item.status == AnalysisStatus.PENDING for item in other_analyses):
            return

        if any(item.status == AnalysisStatus.QUEUED for item in other_analyses):
            await self._docs.update_status(doc, DocumentStatus.QUEUED)
            return

        if any(item.status == AnalysisStatus.COMPLETED for item in other_analyses):
            await self._docs.update_status(doc, DocumentStatus.PROCESSED)
            return

        await self._docs.update_status(doc, DocumentStatus.UPLOADED)
