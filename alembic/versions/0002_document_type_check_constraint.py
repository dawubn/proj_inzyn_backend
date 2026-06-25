"""Add CHECK constraint on documents.document_type

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-25 13:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_DOCUMENT_TYPES = (
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


def upgrade() -> None:
    values = ", ".join(f"'{v}'" for v in _ALLOWED_DOCUMENT_TYPES)
    op.create_check_constraint(
        "ck_documents_document_type",
        "documents",
        f"document_type IN ({values})",
    )


def downgrade() -> None:
    op.drop_constraint("ck_documents_document_type", "documents", type_="check")

