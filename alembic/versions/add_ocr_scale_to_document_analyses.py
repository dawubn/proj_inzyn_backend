"""Add ocr_scale column to document_analyses

Revision ID: ocr_scale_001
Revises: 9a81552f7f7e
Create Date: 2026-06-18 21:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ocr_scale_001"
down_revision: str | None = "9a81552f7f7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_analyses",
        sa.Column("ocr_scale", sa.Float(), nullable=False, server_default="1.0"),
    )


def downgrade() -> None:
    op.drop_column("document_analyses", "ocr_scale")
