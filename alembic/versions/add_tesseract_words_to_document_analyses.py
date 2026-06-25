"""Add tesseract_words column to document_analyses

Revision ID: tesseract_words_001
Revises: ocr_scale_001
Create Date: 2026-06-19 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "tesseract_words_001"
down_revision: str | None = "ocr_scale_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_analyses",
        sa.Column("tesseract_words", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_analyses", "tesseract_words")
