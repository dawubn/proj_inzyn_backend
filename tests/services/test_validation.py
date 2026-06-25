import uuid

from app.enums.analysis import ValidationSeverity
from app.models.validation_rule import ValidationRule
from app.services.validation import RuleEngineService


def test_required_field_uses_configured_user_message() -> None:
    rule = ValidationRule(
        profile_id=uuid.uuid4(),
        name="Podpis",
        rule_type="required_field",
        field_name="has_signature",
        rule_config={"message": "Brakuje podpisu albo oznaczenia miejsca na podpis."},
        severity=ValidationSeverity.ERROR,
        is_active=True,
        order=10,
    )

    issues = RuleEngineService().run([rule], {"has_signature": False})

    assert len(issues) == 1
    assert issues[0].message == "Brakuje podpisu albo oznaczenia miejsca na podpis."
    assert issues[0].field_name == "has_signature"


def test_inactive_rule_is_skipped() -> None:
    rule = ValidationRule(
        profile_id=uuid.uuid4(),
        name="Data dokumentu",
        rule_type="required_field",
        field_name="has_date",
        rule_config={},
        severity=ValidationSeverity.ERROR,
        is_active=False,
        order=10,
    )

    assert RuleEngineService().run([rule], {"has_date": False}) == []
