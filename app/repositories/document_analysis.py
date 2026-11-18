import uuid

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums.analysis import ACTIVE_STATUSES, AnalysisStatus
from app.models.document_analysis import DocumentAnalysis
from app.repositories.base import BaseRepository


class DocumentAnalysisRepository(BaseRepository[DocumentAnalysis]):
    model = DocumentAnalysis

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_document(self, document_id: uuid.UUID) -> list[DocumentAnalysis]:
        result = await self.session.execute(
            select(DocumentAnalysis)
            .where(DocumentAnalysis.document_id == document_id)
            .order_by(DocumentAnalysis.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_task_id(self, task_id: str) -> DocumentAnalysis | None:
        result = await self.session.execute(
            select(DocumentAnalysis).where(DocumentAnalysis.task_id == task_id)
        )
        return result.scalar_one_or_none()

    async def lock_owner(self, owner_id: uuid.UUID) -> None:
        """Serialize queue operations for an owner until the transaction ends."""
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(CAST(:owner_id AS text), 0))"),
            {"owner_id": str(owner_id)},
        )

    async def get_by_id_for_update(
        self,
        analysis_id: uuid.UUID,
    ) -> DocumentAnalysis | None:
        result = await self.session.execute(
            select(DocumentAnalysis)
            .where(DocumentAnalysis.id == analysis_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_active_for_owner(
        self,
        owner_id: uuid.UUID,
    ) -> DocumentAnalysis | None:
        result = await self.session.execute(
            select(DocumentAnalysis)
            .where(
                DocumentAnalysis.owner_id == owner_id,
                DocumentAnalysis.status.in_(ACTIVE_STATUSES),
            )
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()

    async def get_next_queued_for_owner(
        self,
        owner_id: uuid.UUID,
    ) -> DocumentAnalysis | None:
        """Call after lock_owner within the same transaction."""
        result = await self.session.execute(
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
        )
        return result.scalar_one_or_none()

    async def count_queued_before(self, analysis: DocumentAnalysis) -> int:
        """Count waiting entries preceding this analysis in its owner's queue."""
        if analysis.status != AnalysisStatus.QUEUED:
            return 0

        if analysis.queued_at is None:
            precedes = or_(
                DocumentAnalysis.queued_at.is_not(None),
                and_(
                    DocumentAnalysis.queued_at.is_(None),
                    DocumentAnalysis.id < analysis.id,
                ),
            )
        else:
            precedes = or_(
                DocumentAnalysis.queued_at < analysis.queued_at,
                and_(
                    DocumentAnalysis.queued_at == analysis.queued_at,
                    DocumentAnalysis.id < analysis.id,
                ),
            )

        result = await self.session.execute(
            select(func.count())
            .select_from(DocumentAnalysis)
            .where(
                DocumentAnalysis.owner_id == analysis.owner_id,
                DocumentAnalysis.status == AnalysisStatus.QUEUED,
                precedes,
            )
        )
        return int(result.scalar_one())

    async def get_queue_position(
        self,
        analysis: DocumentAnalysis,
    ) -> int | None:
        """Return a one-based position among waiting entries, or None."""
        if analysis.status != AnalysisStatus.QUEUED:
            return None

        return await self.count_queued_before(analysis) + 1

    async def list_owners_with_queued(self) -> list[uuid.UUID]:
        result = await self.session.execute(
            select(DocumentAnalysis.owner_id)
            .where(DocumentAnalysis.status == AnalysisStatus.QUEUED)
            .distinct()
            .order_by(DocumentAnalysis.owner_id.asc())
        )
        return list(result.scalars().all())
