"""Limit document types to the categories the analysis supports

Revision ID: doc_types_002
Revises: scrum162_password_reset
Create Date: 2026-11-17 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "doc_types_002"
down_revision: str | None = "scrum162_password_reset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED_DOCUMENT_TYPES = (
    "unknown",
    "lawsuit",
    "power_of_attorney",
    "application",
    "invoice",
    "contract",
    "patent",
    "demand_letter",
)

_PREVIOUS_ALLOWED_DOCUMENT_TYPES = (
    "unknown",
    "lawsuit",
    "power_of_attorney",
    "application",
    "invoice",
    "contract",
    "id_card",
    "passport",
    "bank_statement",
    "tax_form",
    "financial_report",
    "government_tender",
    "law_and_regulation",
    "manual",
    "patent",
    "scientific_article",
    "other",
)


def _constraint_sql(values: tuple[str, ...]) -> str:
    allowed_values = ", ".join(f"'{value}'" for value in values)
    return f"document_type IN ({allowed_values})"


def upgrade() -> None:
    documents = sa.table(
        "documents", sa.column("document_type"), sa.column("suggested_document_type")
    )
    profiles = sa.table("validation_profiles", sa.column("document_type"))
    # "application" used to mean a request to an authority and now means a job application.
    dropped = ("application",)
    op.drop_constraint("ck_documents_document_type", "documents", type_="check")
    op.execute(
        documents.update()
        .where(
            documents.c.document_type.not_in(_ALLOWED_DOCUMENT_TYPES)
            | documents.c.document_type.in_(dropped)
        )
        .values(document_type="unknown")
    )
    op.execute(
        documents.update()
        .where(
            documents.c.suggested_document_type.not_in(_ALLOWED_DOCUMENT_TYPES)
            | documents.c.suggested_document_type.in_(dropped)
        )
        .values(suggested_document_type=None)
    )
    # Rules go with their profile (ON DELETE CASCADE).
    op.execute(
        profiles.delete().where(
            profiles.c.document_type.not_in(_ALLOWED_DOCUMENT_TYPES)
            | profiles.c.document_type.in_(dropped)
        )
    )
    op.create_check_constraint(
        "ck_documents_document_type", "documents", _constraint_sql(_ALLOWED_DOCUMENT_TYPES)
    )


def downgrade() -> None:
    documents = sa.table("documents", sa.column("document_type"))
    op.drop_constraint("ck_documents_document_type", "documents", type_="check")
    op.execute(
        documents.update()
        .where(documents.c.document_type == "demand_letter")
        .values(document_type="unknown")
    )
    op.create_check_constraint(
        "ck_documents_document_type",
        "documents",
        _constraint_sql(_PREVIOUS_ALLOWED_DOCUMENT_TYPES),
    )
