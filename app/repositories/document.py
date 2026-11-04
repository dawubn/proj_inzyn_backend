import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums.document import DocumentStatus, DocumentType
from app.models.document import Document
from app.repositories.base import BaseRepository


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_owner(
        self, owner_id: uuid.UUID, *, offset: int = 0, limit: int = 20
    ) -> tuple[list[Document], int]:
        stmt = (
            select(Document)
            .where(Document.owner_id == owner_id)
            .order_by(Document.created_at.desc())
        )
        return await self._paginate(stmt, offset=offset, limit=limit)

    async def list_all(self, *, offset: int = 0, limit: int = 20) -> tuple[list[Document], int]:
        stmt = select(Document).order_by(Document.created_at.desc())
        return await self._paginate(stmt, offset=offset, limit=limit)

    async def update_status(self, document: Document, status: DocumentStatus) -> Document:
        document.status = status
        await self.session.flush()
        return document

    async def update_document_type(
        self, document: Document, document_type: DocumentType
    ) -> Document:
        document.document_type = document_type
        return await self.update(document)

    async def update_suggested_document_type(
        self, document: Document, suggested_document_type: str | None
    ) -> Document:
        document.suggested_document_type = suggested_document_type  # type: ignore[assignment]
        return await self.update(document)
