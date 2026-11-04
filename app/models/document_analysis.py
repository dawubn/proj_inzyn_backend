from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.analysis import AnalysisStatus, ProcessingStage
from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.analysis_report import AnalysisReport
    from app.models.document import Document


class DocumentAnalysis(BaseModel):
    __tablename__ = "document_analyses"

    __table_args__ = (
        Index(
            "uq_document_analyses_active_owner",
            "owner_id",
            unique=True,
            postgresql_where=text(
                "status IN ("
                "'in_progress', "
                "'cancelling', "
                "'ocr_in_progress', "
                "'ocr_completed', "
                "'classifying', "
                "'validating'"
                ")"
            ),
        ),
        Index(
            "ix_document_analyses_owner_queue",
            "owner_id",
            "status",
            "queued_at",
            "id",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    status: Mapped[AnalysisStatus] = mapped_column(
        String(50),
        nullable=False,
        default=AnalysisStatus.PENDING,
        index=True,
    )
    processing_stage: Mapped[ProcessingStage] = mapped_column(
        String(50),
        nullable=False,
        default=ProcessingStage.PENDING,
        index=True,
    )
    processing_step: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )

    # Dane potrzebne do uruchomienia oczekującego pipeline'u.
    pipeline: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    pipeline_params: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    queued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Samo żądanie anulowania nie zwalnia slotu użytkownika.
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ocr_raw_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    ocr_provider: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    ocr_scale: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )
    tesseract_words: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    detected_document_type: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    classification_confidence: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    extracted_fields: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    legal_analysis_result: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    document: Mapped[Document] = relationship(
        back_populates="analyses",
        lazy="noload",
    )
    report: Mapped[AnalysisReport | None] = relationship(
        back_populates="analysis",
        lazy="noload",
        uselist=False,
    )

    def __repr__(self) -> str:
        return (
            f"<DocumentAnalysis id={self.id} "
            f"document_id={self.document_id} status={self.status}>"
        )