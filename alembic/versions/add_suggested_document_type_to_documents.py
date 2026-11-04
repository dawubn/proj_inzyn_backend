"""Add suggested_document_type column to documents

Revision ID: suggested_doc_type_001
Revises: tesseract_words_001
Create Date: 2026-06-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "suggested_doc_type_001"
down_revision: str | tuple[str, ...] | None = ("0002", "3f4a5b6c7d8e")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("suggested_document_type", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("documents", "suggested_document_type")
