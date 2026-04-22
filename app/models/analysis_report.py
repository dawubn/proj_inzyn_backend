import uuid
from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.enums.analysis import ReportStatus
from app.models.base import BaseModel


class AnalysisReport(BaseModel):
    __tablename__ = "analysis_reports"

    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("document_analyses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    status: Mapped[ReportStatus] = mapped_column(
        String(50), nullable=False, default=ReportStatus.PENDING, index=True
    )

    # Validation results
    errors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    infos: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_complete: Mapped[bool | None] = mapped_column(nullable=True)
    completeness_score: Mapped[float | None] = mapped_column(nullable=True)

    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    analysis: Mapped["DocumentAnalysis"] = relationship(back_populates="report", lazy="noload")

    def __repr__(self) -> str:
        return f"<AnalysisReport id={self.id} status={self.status} complete={self.is_complete}>"
