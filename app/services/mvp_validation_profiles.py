from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from app.enums.analysis import ValidationSeverity
from app.enums.document import DocumentType
from app.models.validation_rule import ValidationRule


@dataclass(frozen=True)
class ValidationRuleDefinition:
    name: str
    field_name: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    description: str | None = None
    rule_type: str = "required_field"
    rule_config: dict[str, Any] = field(default_factory=dict)
    order: int = 0

    def config(self) -> dict[str, Any]:
        return {"message": self.message, **self.rule_config}


@dataclass(frozen=True)
class ValidationProfileDefinition:
    name: str
    description: str
    document_type: DocumentType
    rules: tuple[ValidationRuleDefinition, ...]


MVP_VALIDATION_PROFILES: tuple[ValidationProfileDefinition, ...] = (
    ValidationProfileDefinition(
        name="MVP - umowa",
        description="Minimalna walidacja formalna umowy: data, podpis i podstawowe sekcje.",
        document_type=DocumentType.CONTRACT,
        rules=(
            ValidationRuleDefinition(
                name="Data dokumentu",
                field_name="has_date",
                message="Brakuje daty sporządzenia lub zawarcia umowy.",
                order=10,
            ),
            ValidationRuleDefinition(
                name="Oznaczenie stron umowy",
                field_name="has_parties_section",
                message="Brakuje sekcji z oznaczeniem stron umowy.",
                order=20,
            ),
            ValidationRuleDefinition(
                name="Przedmiot umowy",
                field_name="has_subject_section",
                message="Brakuje sekcji opisującej przedmiot umowy.",
                order=30,
            ),
            ValidationRuleDefinition(
                name="Podpis",
                field_name="has_signature",
                message="Brakuje podpisu albo oznaczenia miejsca na podpis.",
                order=40,
            ),
            ValidationRuleDefinition(
                name="Załączniki",
                field_name="has_attachments_section",
                message="Nie wykryto sekcji załączników; sprawdź ręcznie, czy są wymagane.",
                severity=ValidationSeverity.WARNING,
                order=50,
            ),
        ),
    ),
    ValidationProfileDefinition(
        name="MVP - pozew",
        description="Minimalna walidacja formalna pozwu: sąd, strony, żądanie, uzasadnienie.",
        document_type=DocumentType.LAWSUIT,
        rules=(
            ValidationRuleDefinition(
                name="Data dokumentu",
                field_name="has_date",
                message="Brakuje daty sporządzenia pozwu.",
                order=10,
            ),
            ValidationRuleDefinition(
                name="Oznaczenie sądu",
                field_name="has_court_section",
                message="Brakuje oznaczenia sądu.",
                order=20,
            ),
            ValidationRuleDefinition(
                name="Oznaczenie stron",
                field_name="has_parties_section",
                message="Brakuje oznaczenia stron postępowania.",
                order=30,
            ),
            ValidationRuleDefinition(
                name="Żądanie pozwu",
                field_name="has_claim_section",
                message="Brakuje żądania pozwu lub wartości przedmiotu sporu.",
                order=40,
            ),
            ValidationRuleDefinition(
                name="Uzasadnienie",
                field_name="has_justification_section",
                message="Brakuje sekcji uzasadnienia pozwu.",
                order=50,
            ),
            ValidationRuleDefinition(
                name="Podpis",
                field_name="has_signature",
                message="Brakuje podpisu albo oznaczenia miejsca na podpis.",
                order=60,
            ),
            ValidationRuleDefinition(
                name="Załączniki",
                field_name="has_attachments_section",
                message="Nie wykryto sekcji załączników; sprawdź ręcznie, czy są wymagane.",
                severity=ValidationSeverity.WARNING,
                order=70,
            ),
        ),
    ),
    ValidationProfileDefinition(
        name="MVP - pełnomocnictwo",
        description="Minimalna walidacja formalna pełnomocnictwa: strony, zakres, data, podpis.",
        document_type=DocumentType.POWER_OF_ATTORNEY,
        rules=(
            ValidationRuleDefinition(
                name="Data dokumentu",
                field_name="has_date",
                message="Brakuje daty sporządzenia pełnomocnictwa.",
                order=10,
            ),
            ValidationRuleDefinition(
                name="Mocodawca",
                field_name="has_principal_section",
                message="Brakuje danych mocodawcy.",
                order=20,
            ),
            ValidationRuleDefinition(
                name="Pełnomocnik",
                field_name="has_attorney_section",
                message="Brakuje danych pełnomocnika.",
                order=30,
            ),
            ValidationRuleDefinition(
                name="Zakres umocowania",
                field_name="has_authorization_scope_section",
                message="Brakuje zakresu pełnomocnictwa lub treści umocowania.",
                order=40,
            ),
            ValidationRuleDefinition(
                name="Podpis",
                field_name="has_signature",
                message="Brakuje podpisu mocodawcy albo oznaczenia miejsca na podpis.",
                order=50,
            ),
        ),
    ),
)


def get_mvp_profile_definition(
    document_type: DocumentType,
) -> ValidationProfileDefinition | None:
    for profile in MVP_VALIDATION_PROFILES:
        if profile.document_type == document_type:
            return profile
    return None


def build_rule_models(
    profile: ValidationProfileDefinition,
    profile_id: uuid.UUID | None = None,
) -> list[ValidationRule]:
    rule_profile_id = profile_id or uuid.uuid4()
    return [
        ValidationRule(
            profile_id=rule_profile_id,
            name=rule.name,
            description=rule.description,
            rule_type=rule.rule_type,
            field_name=rule.field_name,
            rule_config=rule.config(),
            severity=rule.severity,
            is_active=True,
            order=rule.order,
        )
        for rule in profile.rules
    ]
