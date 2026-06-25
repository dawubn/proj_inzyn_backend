from pydantic import Field

from app.enums.document import DocumentStatus, DocumentType, FileExtension
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class DocumentCreate(OrmBase):
    description: str | None = Field(default=None, max_length=1000)


class DocumentUpdate(OrmBase):
    description: str | None = Field(default=None, max_length=1000)
    document_type: DocumentType | None = None


class DocumentResponse(UUIDSchema, TimestampSchema):
    original_filename: str
    file_extension: FileExtension
    file_size_bytes: int
    mime_type: str
    status: DocumentStatus
    document_type: DocumentType
    suggested_document_type: DocumentType | None
    description: str | None


class DocumentListResponse(UUIDSchema):
    original_filename: str
    status: DocumentStatus
    document_type: DocumentType
    suggested_document_type: DocumentType | None
    file_size_bytes: int
    created_at: str
