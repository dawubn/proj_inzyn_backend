import uuid

from app.enums.analysis import AnalysisStatus
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class DocumentAnalysisResponse(UUIDSchema, TimestampSchema):
    document_id: uuid.UUID
    task_id: str | None
    status: AnalysisStatus
    ocr_provider: str | None
    detected_document_type: str | None
    classification_confidence: float | None
    extracted_fields: dict | None
    error_message: str | None


class TriggerAnalysisResponse(OrmBase):
    analysis_id: uuid.UUID
    task_id: str
    message: str
