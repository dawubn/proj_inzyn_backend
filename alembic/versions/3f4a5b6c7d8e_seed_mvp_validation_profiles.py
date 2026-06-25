"""Seed MVP validation profiles

Revision ID: 3f4a5b6c7d8e
Revises: 8d56f94fed2e, tesseract_words_001
Create Date: 2026-06-19 00:00:00.000000

"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3f4a5b6c7d8e"
down_revision: str | Sequence[str] | None = ("8d56f94fed2e", "tesseract_words_001")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTRACT_PROFILE_ID = UUID("11111111-1111-4111-8111-111111111111")
LAWSUIT_PROFILE_ID = UUID("22222222-2222-4222-8222-222222222222")
POWER_OF_ATTORNEY_PROFILE_ID = UUID("33333333-3333-4333-8333-333333333333")

validation_profiles = sa.table(
    "validation_profiles",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("document_type", sa.String),
    sa.column("is_active", sa.Boolean),
)

validation_rules = sa.table(
    "validation_rules",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("profile_id", postgresql.UUID(as_uuid=True)),
    sa.column("name", sa.String),
    sa.column("description", sa.Text),
    sa.column("rule_type", sa.String),
    sa.column("field_name", sa.String),
    sa.column("rule_config", postgresql.JSONB()),
    sa.column("severity", sa.String),
    sa.column("is_active", sa.Boolean),
    sa.column("order", sa.Integer),
)


def _rule(  # noqa: PLR0913
    rule_id: str,
    profile_id: UUID,
    name: str,
    field_name: str,
    message: str,
    order: int,
    severity: str = "error",
) -> dict[str, object]:
    return {
        "id": UUID(rule_id),
        "profile_id": profile_id,
        "name": name,
        "description": None,
        "rule_type": "required_field",
        "field_name": field_name,
        "rule_config": {"message": message},
        "severity": severity,
        "is_active": True,
        "order": order,
    }


def upgrade() -> None:
    op.bulk_insert(
        validation_profiles,
        [
            {
                "id": CONTRACT_PROFILE_ID,
                "name": "MVP - umowa",
                "description": (
                    "Minimalna walidacja formalna umowy: data, podpis i podstawowe sekcje."
                ),
                "document_type": "contract",
                "is_active": True,
            },
            {
                "id": LAWSUIT_PROFILE_ID,
                "name": "MVP - pozew",
                "description": (
                    "Minimalna walidacja formalna pozwu: sąd, strony, żądanie, uzasadnienie."
                ),
                "document_type": "lawsuit",
                "is_active": True,
            },
            {
                "id": POWER_OF_ATTORNEY_PROFILE_ID,
                "name": "MVP - pełnomocnictwo",
                "description": (
                    "Minimalna walidacja formalna pełnomocnictwa: strony, zakres, data, podpis."
                ),
                "document_type": "power_of_attorney",
                "is_active": True,
            },
        ],
    )

    op.bulk_insert(
        validation_rules,
        [
            _rule(
                "11111111-1111-4111-8111-111111111101",
                CONTRACT_PROFILE_ID,
                "Data dokumentu",
                "has_date",
                "Brakuje daty sporządzenia lub zawarcia umowy.",
                10,
            ),
            _rule(
                "11111111-1111-4111-8111-111111111102",
                CONTRACT_PROFILE_ID,
                "Oznaczenie stron umowy",
                "has_parties_section",
                "Brakuje sekcji z oznaczeniem stron umowy.",
                20,
            ),
            _rule(
                "11111111-1111-4111-8111-111111111103",
                CONTRACT_PROFILE_ID,
                "Przedmiot umowy",
                "has_subject_section",
                "Brakuje sekcji opisującej przedmiot umowy.",
                30,
            ),
            _rule(
                "11111111-1111-4111-8111-111111111104",
                CONTRACT_PROFILE_ID,
                "Podpis",
                "has_signature",
                "Brakuje podpisu albo oznaczenia miejsca na podpis.",
                40,
            ),
            _rule(
                "11111111-1111-4111-8111-111111111105",
                CONTRACT_PROFILE_ID,
                "Załączniki",
                "has_attachments_section",
                "Nie wykryto sekcji załączników; sprawdź ręcznie, czy są wymagane.",
                50,
                "warning",
            ),
            _rule(
                "22222222-2222-4222-8222-222222222201",
                LAWSUIT_PROFILE_ID,
                "Data dokumentu",
                "has_date",
                "Brakuje daty sporządzenia pozwu.",
                10,
            ),
            _rule(
                "22222222-2222-4222-8222-222222222202",
                LAWSUIT_PROFILE_ID,
                "Oznaczenie sądu",
                "has_court_section",
                "Brakuje oznaczenia sądu.",
                20,
            ),
            _rule(
                "22222222-2222-4222-8222-222222222203",
                LAWSUIT_PROFILE_ID,
                "Oznaczenie stron",
                "has_parties_section",
                "Brakuje oznaczenia stron postępowania.",
                30,
            ),
            _rule(
                "22222222-2222-4222-8222-222222222204",
                LAWSUIT_PROFILE_ID,
                "Żądanie pozwu",
                "has_claim_section",
                "Brakuje żądania pozwu lub wartości przedmiotu sporu.",
                40,
            ),
            _rule(
                "22222222-2222-4222-8222-222222222205",
                LAWSUIT_PROFILE_ID,
                "Uzasadnienie",
                "has_justification_section",
                "Brakuje sekcji uzasadnienia pozwu.",
                50,
            ),
            _rule(
                "22222222-2222-4222-8222-222222222206",
                LAWSUIT_PROFILE_ID,
                "Podpis",
                "has_signature",
                "Brakuje podpisu albo oznaczenia miejsca na podpis.",
                60,
            ),
            _rule(
                "22222222-2222-4222-8222-222222222207",
                LAWSUIT_PROFILE_ID,
                "Załączniki",
                "has_attachments_section",
                "Nie wykryto sekcji załączników; sprawdź ręcznie, czy są wymagane.",
                70,
                "warning",
            ),
            _rule(
                "33333333-3333-4333-8333-333333333301",
                POWER_OF_ATTORNEY_PROFILE_ID,
                "Data dokumentu",
                "has_date",
                "Brakuje daty sporządzenia pełnomocnictwa.",
                10,
            ),
            _rule(
                "33333333-3333-4333-8333-333333333302",
                POWER_OF_ATTORNEY_PROFILE_ID,
                "Mocodawca",
                "has_principal_section",
                "Brakuje danych mocodawcy.",
                20,
            ),
            _rule(
                "33333333-3333-4333-8333-333333333303",
                POWER_OF_ATTORNEY_PROFILE_ID,
                "Pełnomocnik",
                "has_attorney_section",
                "Brakuje danych pełnomocnika.",
                30,
            ),
            _rule(
                "33333333-3333-4333-8333-333333333304",
                POWER_OF_ATTORNEY_PROFILE_ID,
                "Zakres umocowania",
                "has_authorization_scope_section",
                "Brakuje zakresu pełnomocnictwa lub treści umocowania.",
                40,
            ),
            _rule(
                "33333333-3333-4333-8333-333333333305",
                POWER_OF_ATTORNEY_PROFILE_ID,
                "Podpis",
                "has_signature",
                "Brakuje podpisu mocodawcy albo oznaczenia miejsca na podpis.",
                50,
            ),
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM validation_rules "
            "WHERE profile_id IN (:contract_id, :lawsuit_id, :power_of_attorney_id)"
        ).bindparams(
            contract_id=CONTRACT_PROFILE_ID,
            lawsuit_id=LAWSUIT_PROFILE_ID,
            power_of_attorney_id=POWER_OF_ATTORNEY_PROFILE_ID,
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM validation_profiles "
            "WHERE id IN (:contract_id, :lawsuit_id, :power_of_attorney_id)"
        ).bindparams(
            contract_id=CONTRACT_PROFILE_ID,
            lawsuit_id=LAWSUIT_PROFILE_ID,
            power_of_attorney_id=POWER_OF_ATTORNEY_PROFILE_ID,
        )
    )
