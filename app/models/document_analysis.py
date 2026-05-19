from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.analysis import AnalysisStatus
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.analysis_report import AnalysisReport
    from app.models.document import Document


class DocumentAnalysis(BaseModel):
    __tablename__ = "document_analyses"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[AnalysisStatus] = mapped_column(
        String(50), nullable=False, default=AnalysisStatus.PENDING, index=True
    )

    ocr_raw_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    ocr_provider: Mapped[str | None] = mapped_column(String(100), nullable=True)
    detected_document_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extracted_fields: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    document: Mapped[Document] = relationship(back_populates="analyses", lazy="noload")
    report: Mapped[AnalysisReport | None] = relationship(
        back_populates="analysis", lazy="noload", uselist=False
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentAnalysis id={self.id} document_id={self.document_id} status={self.status}>"
        )
