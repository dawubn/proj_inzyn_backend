import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums.document import DocumentStatus
from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_owner(
        self, owner_id: uuid.UUID, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[Document], int]:
        stmt = select(Document).where(Document.owner_id == owner_id).order_by(Document.created_at.desc())
        return await self._paginate(stmt, offset=offset, limit=limit)

    async def update_status(self, document: Document, status: DocumentStatus) -> Document:
        document.status = status
        await self.session.flush()
        return document
