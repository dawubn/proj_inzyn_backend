"""Cross-field rules, dependency graphs and real extraction-to-report integration."""

from typing import Any

import pytest

from app.enums.document import DocumentType
from app.models.validation_rule import ValidationRule
from app.services.formal_report import FormalValidationService
from app.services.formal_validation import FormalDocumentExtractor
from app.services.mvp_validation_profiles import build_rule_models, get_mvp_profile_definition
from app.services.validation import RuleEngineService
from tests.services.test_formal_validation_v2 import azure
from tests.services.test_formal_validation_v2 import rule as base_rule


def rule(
    field: str, kind: str = "required_field", config: dict[str, Any] | None = None
) -> ValidationRule:
    item = base_rule(field, kind, config)
    item.name = field
    return item


def comparison(**config: Any) -> ValidationRule:
    return rule(
        "end",
        "compare_fields",
        {
            "other_field": "start",
            "operator": "gte",
            "value_type": "date",
            **config,
        },
    )


def values(start: str = "2026-01-01", end: str = "2026-12-31") -> dict[str, Any]:
    return {
        "start": start,
        "end": end,
        "observations": {
            "start": {"state": "present", "evidence": [{"text": str(start)}]},
            "end": {"state": "present", "evidence": [{"text": str(end)}]},
        },
    }


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-01-01", "2026-12-31", "pass"),
        ("2026-12-31", "2026-01-01", "fail"),
        ("2026-01-01", "2026-01-01", "pass"),
        ("2026-02-30", "2026-03-01", "unknown"),
    ],
)
def test_date_comparison(start, end, expected):
    result = RuleEngineService().evaluate([comparison()], values(start, end))[0]
    assert result.outcome == expected
    assert len(result.evidence) == 2


@pytest.mark.parametrize("state", ["masked", "unknown", "empty", "missing", "invalid"])
def test_unreadable_operand_is_never_a_contradiction(state):
    data = values()
    data["observations"]["end"]["state"] = state
    assert RuleEngineService().evaluate([comparison()], data)[0].outcome == "unknown"


@pytest.mark.parametrize(
    "kind,start,end,expected",
    [
        ("number", "0.10", "0.1", "pass"),
        ("number", "NaN", "1", "unknown"),
        ("text", "  Anna  Nowak ", "anna nowak", "pass"),
        ("text", "Anna Nowak", "Jan Nowak", "fail"),
    ],
)
def test_typed_comparison(kind, start, end, expected):
    result = RuleEngineService().evaluate(
        [comparison(value_type=kind, operator="eq")], values(start, end)
    )[0]
    assert result.outcome == expected


def test_dependency_is_evaluated_before_dependent_regardless_of_order():
    parent = rule("start")
    child = comparison(depends_on=[parent.name])
    result = RuleEngineService().evaluate([child, parent], values())
    assert [r.outcome for r in result] == ["pass", "pass"]


@pytest.mark.parametrize("dependencies", [["missing"], "start", [123]])
def test_invalid_dependency_is_reported(dependencies):
    assert (
        RuleEngineService().evaluate([comparison(depends_on=dependencies)], values())[0].outcome
        == "error"
    )


def test_cycle_does_not_crash_or_pass():
    a, b = rule("start"), rule("end")
    a.rule_config = {"depends_on": [b.name]}
    b.rule_config = {"depends_on": [a.name]}
    assert [r.outcome for r in RuleEngineService().evaluate([a, b], values())] == ["error", "error"]


def test_failed_prerequisite_blocks_comparison():
    parent = rule("start")
    data = values()
    data["observations"]["start"]["state"] = "missing"
    child = comparison(depends_on=[parent.name])
    results = RuleEngineService().evaluate([child, parent], data)
    assert results[0].reason_code == "DEPENDENCY_UNRESOLVED"


@pytest.mark.parametrize(
    "flag,expected", [(True, "fail"), (False, "not_applicable"), (None, "unknown")]
)
def test_conditional_requirement(flag, expected):
    item = rule("attorney")
    item.rule_config = {"when": {"field": "represented", "equals": True}}
    assert RuleEngineService().evaluate([item], {"represented": flag})[0].outcome == expected


@pytest.mark.parametrize(
    "kind,start_label,end_label",
    [
        (DocumentType.CONTRACT, "Data rozpoczęcia umowy", "Data zakończenia umowy"),
    ],
)
def test_conflict_survives_extraction_and_reporting(kind, start_label, end_label):
    text = (
        f"{start_label}: 20.12.2026\n{end_label}: 01.01.2026\n"
        "Dokument zawiera dodatkowe informacje identyfikacyjne."
    )
    fields = FormalDocumentExtractor().extract(azure(text), kind).fields
    profile = get_mvp_profile_definition(kind)
    assert profile is not None
    issues, summary = FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, kind
    )
    conflicts = [i for i in issues if i.details.get("reason_code") == "FIELDS_INCONSISTENT"]
    assert len(conflicts) == 1
    assert len(conflicts[0].details["evidence"]) == 2
    assert any(r["reason_code"] == "FIELDS_INCONSISTENT" for r in summary["rule_results"])
    assert any(r["rule_type"] == "compare_fields" for r in summary["profile_snapshot"])


@pytest.mark.parametrize(
    "end,confidence,expected",
    [
        ("01.01.2027", 0.99, "pass"),
        ("[UTAJNIONO DATA]", 0.99, "unknown"),
        ("01.01.2026", 0.4, "unknown"),
        ("31.02.2026", 0.99, "unknown"),
        ("...........", 0.99, "unknown"),
    ],
)
def test_date_roles_are_conservative(end, confidence, expected):
    text = (
        f"Data rozpoczęcia: 20.12.2026\nData zakończenia: {end}\n"
        "Treść umowy dotyczącej świadczenia usług przez strony."
    )
    fields = (
        FormalDocumentExtractor().extract(azure(text, confidence), DocumentType.CONTRACT).fields
    )
    profile = get_mvp_profile_definition(DocumentType.CONTRACT)
    assert profile is not None
    _, summary = FormalValidationService().build_report(
        build_rule_models(profile), fields, profile.name, profile.document_type
    )
    assert (
        next(r for r in summary["rule_results"] if r["field_name"] == "period_end")["outcome"]
        == expected
    )


def test_dates_in_prose_are_not_compared():
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure("Umowa zawarta dnia 20.12.2026. Przepis obowiązuje od 01.01.2020."),
            DocumentType.CONTRACT,
        )
        .fields
    )
    assert "period_start" not in fields
    assert "period_end" not in fields


@pytest.mark.parametrize("dependencies", [["absent"], ["end"], "end"])
def test_profile_rejects_invalid_dependencies(dependencies):
    from pydantic import ValidationError

    from app.schemas.validation_profile import ValidationProfileCreate

    with pytest.raises(ValidationError):
        ValidationProfileCreate.model_validate(
            {
                "name": "Test",
                "document_type": "contract",
                "rules": [
                    {
                        "name": "end",
                        "field_name": "end",
                        "rule_type": "required_field",
                        "rule_config": {"depends_on": dependencies},
                    }
                ],
            }
        )


def test_profile_configuration_roundtrips_conditions_and_dependencies():
    from app.schemas.validation_profile import ValidationProfileCreate

    data: dict[str, Any] = {
        "name": "Test",
        "document_type": "contract",
        "rules": [
            {"name": "start", "field_name": "start", "rule_type": "required_field"},
            {
                "name": "end",
                "field_name": "end",
                "rule_type": "compare_fields",
                "rule_config": {
                    "other_field": "start",
                    "operator": "gte",
                    "value_type": "date",
                    "depends_on": ["start"],
                    "when": {"field": "fixed_term", "equals": True},
                },
            },
        ],
    }
    profile = ValidationProfileCreate.model_validate(data)
    restored = ValidationProfileCreate.model_validate_json(profile.model_dump_json())
    assert restored.rules[1].rule_config == data["rules"][1]["rule_config"]


def test_repeated_contradictory_date_is_uncertain():
    fields = (
        FormalDocumentExtractor()
        .extract(
            azure(
                "Data rozpoczęcia: 01.01.2026\nData rozpoczęcia: 02.01.2026\n"
                "Data zakończenia: 01.01.2027"
            ),
            DocumentType.CONTRACT,
        )
        .fields
    )
    assert fields["observations"]["period_start"]["state"] == "unknown"
    assert fields["period_start"] is None


def test_missing_second_date_is_not_a_confirmed_conflict():
    from app.services.formal_relations import relation_rules

    fields = (
        FormalDocumentExtractor()
        .extract(
            azure("Data rozpoczęcia: 01.01.2026\nUmowa zawarta na czas nieokreślony."),
            DocumentType.CONTRACT,
        )
        .fields
    )
    assert relation_rules(DocumentType.CONTRACT, fields) == []


@pytest.mark.parametrize("config", [{"operator": []}, {"value_type": {}}, {"other_field": 1}])
def test_bad_comparison_config_returns_error(config):
    assert RuleEngineService().evaluate([comparison(**config)], values())[0].outcome == "error"
