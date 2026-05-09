from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.document import DocumentStatus, DocumentType, FileExtension
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.document_analysis import DocumentAnalysis
    from app.models.user import User


class Document(BaseModel):
    __tablename__ = "documents"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    file_extension: Mapped[FileExtension] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        String(50), nullable=False, default=DocumentStatus.UPLOADED, index=True
    )
    document_type: Mapped[DocumentType] = mapped_column(
        String(50), nullable=False, default=DocumentType.UNKNOWN
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped[User] = relationship(back_populates="documents", lazy="noload")
    analyses: Mapped[list[DocumentAnalysis]] = relationship(
        back_populates="document", lazy="noload", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Document id={self.id} filename={self.original_filename} status={self.status}>"
