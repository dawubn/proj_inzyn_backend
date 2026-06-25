import uuid
from typing import Any

from pydantic import computed_field

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
    tesseract_words: list[dict[str, Any]] | None
    legal_analysis_result: dict[str, Any] | None = None

    @property
    def progress_percent(self) -> int:
        return min(int((self.processing_step / 4) * 100), 100)

    @computed_field
    def irregularities_count(self) -> dict[str, int]:
        if not self.legal_analysis_result or not isinstance(self.legal_analysis_result, dict):
            return {"critical": 0, "high": 0, "medium": 0}
        errors = self.legal_analysis_result.get("errors", [])
        if not isinstance(errors, list):
            return {"critical": 0, "high": 0, "medium": 0}
        stats = {"critical": 0, "high": 0, "medium": 0}
        for error in errors:
            if isinstance(error, dict):
                severity = error.get("severity", "medium")
                if severity in stats:
                    stats[severity] += 1
        return stats


class DocumentAnalysisResponse(DocumentAnalysisListResponse):
    """Full response with detailed results (for detail endpoint)."""

    ocr_raw_result: dict[str, Any] | None
    extracted_fields: dict[str, Any] | None


class TriggerAnalysisResponse(OrmBase):
    analysis_id: uuid.UUID
    task_id: str
    message: str
