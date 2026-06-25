import uuid
from pathlib import Path

import aiofiles
import structlog

from app.core.config import settings
from app.core.exceptions import (
    FileTooLargeError,
    ForbiddenError,
    NotFoundError,
    UnsupportedFileTypeError,
)
from app.enums.document import DocumentStatus, DocumentType, FileExtension
from app.models.document import Document
from app.repositories.document import DocumentRepository
from app.schemas.document import DocumentCreate

logger = structlog.get_logger(__name__)


class DocumentService:
    def __init__(self, document_repo: DocumentRepository) -> None:
        self._docs = document_repo

    async def upload(
        self,
        owner_id: uuid.UUID,
        filename: str,
        content: bytes,
        content_type: str,
        metadata: DocumentCreate,
    ) -> Document:
        self._validate_file(filename, len(content))

        ext = Path(filename).suffix.lstrip(".").lower()
        storage_path = self._build_storage_path(owner_id, filename)

        await self._persist_file(storage_path, content)

        doc = Document(
            owner_id=owner_id,
            original_filename=filename,
            storage_path=str(storage_path),
            file_extension=FileExtension(ext),
            file_size_bytes=len(content),
            mime_type=content_type,
            status=DocumentStatus.UPLOADED,
            description=metadata.description,
        )
        created = await self._docs.create(doc)
        logger.info("Document uploaded", document_id=str(created.id), owner_id=str(owner_id))
        return created

    async def update_document_type(
        self,
        document_id: uuid.UUID,
        owner_id: uuid.UUID,
        document_type: DocumentType,
    ) -> Document:
        doc = await self._docs.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        if doc.owner_id != owner_id:
            raise ForbiddenError("You do not own this document")
        return await self._docs.update_document_type(doc, document_type)

    async def get_or_raise(
        self, document_id: uuid.UUID, owner_id: uuid.UUID | None = None
    ) -> Document:
        doc = await self._docs.get_by_id(document_id)
        if not doc:
            raise NotFoundError("Document not found")
        if owner_id and doc.owner_id != owner_id:
            raise NotFoundError("Document not found")
        return doc

    async def list_for_owner(
        self, owner_id: uuid.UUID, *, page: int = 1, page_size: int = 20
    ) -> tuple[list[Document], int]:
        offset = (page - 1) * page_size
        return await self._docs.get_by_owner(owner_id, offset=offset, limit=page_size)

    def _validate_file(self, filename: str, size: int) -> None:
        if size > settings.max_upload_size_bytes:
            raise FileTooLargeError(f"File exceeds limit of {settings.MAX_UPLOAD_SIZE_MB} MB")
        ext = Path(filename).suffix.lstrip(".").lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise UnsupportedFileTypeError(f"Extension .{ext} is not supported")

    def _build_storage_path(self, owner_id: uuid.UUID, filename: str) -> Path:
        safe_name = Path(filename).name
        dest = Path(settings.STORAGE_PATH) / str(owner_id) / safe_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        return dest

    async def _persist_file(self, path: Path, content: bytes) -> None:
        async with aiofiles.open(path, "wb") as f:
            await f.write(content)
