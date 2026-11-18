"""What is known about the Azure OpenAI models a user can pick for the legal analysis.

The figures come from a comparison run on 2026-11-17: the legal analysis prompt of this
app on a 2-page residential lease agreement with 6 planted defects (missing rent, end
date before start date, deposit above the legal limit, termination at will with 1 day of
notice, invalid PESEL, missing signature and date), 2 runs per model. Cost is computed
from the token usage and the official list prices per 1M tokens. A longer document costs
and takes proportionally more. Deployments not listed here are offered without a profile.
"""

from dataclasses import dataclass
from enum import StrEnum


class CostLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SpeedLevel(StrEnum):
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class QualityLevel(StrEnum):
    BASIC = "basic"
    GOOD = "good"
    BEST = "best"


@dataclass(frozen=True)
class AiModelProfile:
    description: str
    cost: CostLevel
    speed: SpeedLevel
    quality: QualityLevel
    # Measured on the test document
    test_cost_usd: float
    test_seconds: int
    test_issues_found: str
    recommended: bool = False
    # Reasoning models accept only the default temperature
    supports_temperature: bool = True


TEST_ISSUES_TOTAL = 6
TEST_DOCUMENT = "2-page lease agreement with 6 planted issues"

AI_MODEL_PROFILES: dict[str, AiModelProfile] = {
    "gpt-6.1-sol": AiModelProfile(
        description=(
            "Most thorough. Found every test issue plus an extra one, with detailed "
            "explanations. Slowest and most expensive."
        ),
        cost=CostLevel.HIGH,
        speed=SpeedLevel.SLOW,
        quality=QualityLevel.BEST,
        test_cost_usd=0.040,
        test_seconds=80,
        test_issues_found="6",
        supports_temperature=False,
    ),
    "gpt-6.1-terra": AiModelProfile(
        description="Best balance. Found every test issue at a moderate cost and time.",
        cost=CostLevel.MEDIUM,
        speed=SpeedLevel.MEDIUM,
        quality=QualityLevel.BEST,
        test_cost_usd=0.016,
        test_seconds=20,
        test_issues_found="6",
        recommended=True,
        supports_temperature=False,
    ),
    "gpt-6-luna": AiModelProfile(
        description=(
            "Very cheap and almost as thorough as the larger models, but slow because "
            "it reasons at length before answering."
        ),
        cost=CostLevel.LOW,
        speed=SpeedLevel.SLOW,
        quality=QualityLevel.GOOD,
        test_cost_usd=0.0024,
        test_seconds=46,
        test_issues_found="5-6",
        supports_temperature=False,
    ),
    "gpt-4.1": AiModelProfile(
        description=(
            "Previous generation. Fast and consistent, but missed some issues in the test. "
            "Azure retires it in April 2027."
        ),
        cost=CostLevel.MEDIUM,
        speed=SpeedLevel.FAST,
        quality=QualityLevel.GOOD,
        test_cost_usd=0.008,
        test_seconds=11,
        test_issues_found="4-5",
    ),
    "gpt-4.1-mini": AiModelProfile(
        description=(
            "Smaller previous-generation model. Cheap and fast; once cited the wrong legal "
            "basis and added generic remarks. Azure retires it in April 2027."
        ),
        cost=CostLevel.LOW,
        speed=SpeedLevel.FAST,
        quality=QualityLevel.GOOD,
        test_cost_usd=0.0019,
        test_seconds=8,
        test_issues_found="4-5",
    ),
    "gpt-4.1-nano": AiModelProfile(
        description=(
            "Cheapest and fastest. Catches only obvious issues and adds generic remarks. "
            "Azure retires it in April 2027."
        ),
        cost=CostLevel.LOW,
        speed=SpeedLevel.FAST,
        quality=QualityLevel.BASIC,
        test_cost_usd=0.0005,
        test_seconds=4,
        test_issues_found="3",
    ),
}


def supports_temperature(model: str) -> bool:
    profile = AI_MODEL_PROFILES.get(model)
    return profile.supports_temperature if profile else True
