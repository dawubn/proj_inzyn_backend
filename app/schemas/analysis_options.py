from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.ai_models import CostLevel, QualityLevel, SpeedLevel
from app.enums.analysis import RedactionCategory

ALL_REDACTION_CATEGORIES: tuple[RedactionCategory, ...] = tuple(RedactionCategory)

# Key under which the options are kept in DocumentAnalysis.pipeline_params
OPTIONS_PARAM = "options"


class AnalysisOptions(BaseModel):
    """How a single analysis should be run. The defaults give the full pipeline."""

    anonymization_enabled: bool = True
    redaction_categories: list[RedactionCategory] = Field(
        default_factory=lambda: list(ALL_REDACTION_CATEGORIES)
    )
    ai_analysis_enabled: bool = True
    # None means the default model from the configuration
    ai_model: str | None = None

    @field_validator("redaction_categories")
    @classmethod
    def _unique_in_fixed_order(cls, value: list[RedactionCategory]) -> list[RedactionCategory]:
        return [category for category in ALL_REDACTION_CATEGORIES if category in value]

    @model_validator(mode="after")
    def _normalize(self) -> "AnalysisOptions":
        if self.anonymization_enabled and not self.redaction_categories:
            raise ValueError("Select at least one data category or turn anonymization off")
        if not self.anonymization_enabled:
            self.redaction_categories = []
        if not self.ai_analysis_enabled:
            self.ai_model = None
        return self

    @classmethod
    def from_pipeline_params(cls, params: dict[str, Any] | None) -> "AnalysisOptions":
        """Options stored with an analysis; analyses created without them use the defaults."""
        stored = (params or {}).get(OPTIONS_PARAM)
        if not isinstance(stored, dict):
            return cls()
        try:
            return cls.model_validate(stored)
        except ValueError:
            return cls()


class AiModelTestResult(BaseModel):
    """How the model did on the comparison document."""

    document: str
    issues_found: str
    issues_total: int
    cost_usd: float
    seconds: int


class AiModelOption(BaseModel):
    """A model that can be picked, with what is known about its cost and quality."""

    name: str
    recommended: bool = False
    description: str | None = None
    cost: CostLevel | None = None
    speed: SpeedLevel | None = None
    quality: QualityLevel | None = None
    test_result: AiModelTestResult | None = None


class AnalysisOptionsResponse(BaseModel):
    """What can be configured for an analysis, used to build the settings panel."""

    redaction_categories: list[RedactionCategory]
    ai_analysis_available: bool
    ai_models: list[AiModelOption]
    default_ai_model: str
