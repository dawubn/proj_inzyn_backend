import uuid
from typing import Any

from pydantic import Field

from app.enums.analysis import ReportStatus, ValidationSeverity
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class ValidationIssue(OrmBase):
    rule_name: str
    field_name: str | None
    severity: ValidationSeverity
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AnalysisReportResponse(UUIDSchema, TimestampSchema):
    analysis_id: uuid.UUID
    status: ReportStatus
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]
    infos: list[ValidationIssue]
    error_count: int
    warning_count: int
    is_complete: bool | None
    completeness_score: float | None
    summary: dict | None
