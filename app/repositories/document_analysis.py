import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
