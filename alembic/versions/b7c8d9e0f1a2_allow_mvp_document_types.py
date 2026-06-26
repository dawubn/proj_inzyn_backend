"""Allow MVP document types in documents constraint

Revision ID: b7c8d9e0f1a2
Revises: suggested_doc_type_001
Create Date: 2026-06-26 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | None = "suggested_doc_type_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALLOWED_DOCUMENT_TYPES = (
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

_PREVIOUS_ALLOWED_DOCUMENT_TYPES = (
    "unknown",
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
    op.drop_constraint("ck_documents_document_type", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_document_type",
        "documents",
        _constraint_sql(_ALLOWED_DOCUMENT_TYPES),
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_document_type", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_document_type",
        "documents",
        _constraint_sql(_PREVIOUS_ALLOWED_DOCUMENT_TYPES),
    )
