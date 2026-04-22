import uuid

from pydantic import Field

from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class ValidationRuleCreate(OrmBase):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    rule_type: str = Field(min_length=1, max_length=100)
    field_name: str | None = None
    rule_config: dict = Field(default_factory=dict)
    severity: ValidationSeverity = ValidationSeverity.ERROR
    order: int = 0


class ValidationRuleResponse(UUIDSchema, TimestampSchema):
    profile_id: uuid.UUID
    name: str
    description: str | None
    rule_type: str
    field_name: str | None
    rule_config: dict
    severity: ValidationSeverity
    is_active: bool
    order: int


class ValidationProfileCreate(OrmBase):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    document_type: DocumentType
    rules: list[ValidationRuleCreate] = Field(default_factory=list)


class ValidationProfileResponse(UUIDSchema, TimestampSchema):
    name: str
    description: str | None
    document_type: DocumentType
    is_active: bool
    rules: list[ValidationRuleResponse] = Field(default_factory=list)
