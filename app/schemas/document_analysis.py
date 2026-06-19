import uuid
from typing import Any

from app.enums.analysis import AnalysisStatus, ProcessingStage
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class DocumentAnalysisListResponse(UUIDSchema, TimestampSchema):
    """Lightweight response for listing analyses (without raw results)."""

    document_id: uuid.UUID
    task_id: str | None
    status: AnalysisStatus
    processing_stage: ProcessingStage
    processing_step: int
    ocr_provider: str | None
    ocr_scale: float
    detected_document_type: str | None
    classification_confidence: float | None
    error_message: str | None

    @property
    def progress_percent(self) -> int:
        return min(int((self.processing_step / 4) * 100), 100)


class DocumentAnalysisResponse(DocumentAnalysisListResponse):
    """Full response with detailed results (for detail endpoint)."""

    ocr_raw_result: dict[str, Any] | None
    extracted_fields: dict[str, Any] | None
    legal_analysis_result: dict[str, Any] | None


class TriggerAnalysisResponse(OrmBase):
    analysis_id: uuid.UUID
    task_id: str
    message: str
