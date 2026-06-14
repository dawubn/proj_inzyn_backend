import uuid
from typing import Any

from app.enums.analysis import AnalysisStatus, ProcessingStage
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class DocumentAnalysisResponse(UUIDSchema, TimestampSchema):
    document_id: uuid.UUID
    task_id: str | None
    status: AnalysisStatus
    processing_stage: ProcessingStage
    processing_step: int  # 0-4 for progress tracking
    ocr_provider: str | None
    ocr_raw_result: dict[str, Any] | None
    detected_document_type: str | None
    classification_confidence: float | None
    extracted_fields: dict[str, Any] | None
    legal_analysis_result: dict[str, Any] | None
    error_message: str | None

    @property
    def progress_percent(self) -> int:
        # Calculate progress percentage based on processing_step.
        # 0=pending(0%), 1=local_ocr(25%), 2=redaction(50%), 3=azure_ocr(75%), 4=llm(100%)
        return min(int((self.processing_step / 4) * 100), 100)


class TriggerAnalysisResponse(OrmBase):
    analysis_id: uuid.UUID
    task_id: str
    message: str
