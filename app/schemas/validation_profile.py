import uuid
from graphlib import CycleError, TopologicalSorter
from typing import Any, Self

from pydantic import Field, model_validator

from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.schemas.common import OrmBase, TimestampSchema, UUIDSchema


class ValidationRuleCreate(OrmBase):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    rule_type: str = Field(min_length=1, max_length=100)
    field_name: str | None = None
    rule_config: dict[str, Any] = Field(default_factory=dict)
    severity: ValidationSeverity = ValidationSeverity.ERROR
    order: int = 0


class ValidationRuleResponse(UUIDSchema, TimestampSchema):
    profile_id: uuid.UUID
    name: str
    description: str | None
    rule_type: str
    field_name: str | None
    rule_config: dict[str, Any]
    severity: ValidationSeverity
    is_active: bool
    order: int


class ValidationProfileCreate(OrmBase):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    document_type: DocumentType
    rules: list[ValidationRuleCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dependencies(self) -> Self:
        names = [rule.name for rule in self.rules]
        graph: dict[str, list[str]] = {}
        for rule in self.rules:
            dependencies = rule.rule_config.get("depends_on", [])
            if not isinstance(dependencies, list) or any(
                not isinstance(name, str) or names.count(name) != 1 for name in dependencies
            ):
                raise ValueError("Dependencies must reference unique rule names in this profile")
            graph[rule.name] = dependencies
            condition = rule.rule_config.get("when")
            if condition is not None and (
                not isinstance(condition, dict)
                or set(condition) != {"field", "equals"}
                or not isinstance(condition.get("field"), str)
                or not condition["field"].strip()
                or not isinstance(condition["equals"], str | bool | int | float)
            ):
                raise ValueError("A condition requires a field and a scalar equals value")
            if rule.rule_type == "compare_fields":
                config = rule.rule_config
                if (
                    not rule.field_name
                    or not isinstance(config.get("other_field"), str)
                    or not config["other_field"].strip()
                    or config.get("operator") not in ("eq", "ne", "lt", "lte", "gt", "gte")
                    or config.get("value_type") not in ("date", "number", "text")
                    or (
                        config.get("value_type") == "text"
                        and config.get("operator") not in ("eq", "ne")
                    )
                ):
                    raise ValueError("Invalid field comparison configuration")
        try:
            tuple(TopologicalSorter(graph).static_order())
        except CycleError as exc:
            raise ValueError("Rule dependencies cannot contain cycles") from exc
        return self


class ValidationProfileResponse(UUIDSchema, TimestampSchema):
    name: str
    description: str | None
    document_type: DocumentType
    is_active: bool
    rules: list[ValidationRuleResponse] = Field(default_factory=list)
