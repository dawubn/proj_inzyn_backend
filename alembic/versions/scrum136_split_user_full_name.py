"""Replace users.full_name with name, surname and company.

Existing full_name values are kept as-is in the name column.

Revision ID: scrum136_user_name
Revises: queue_001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "scrum136_user_name"
down_revision: str | None = "queue_001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("users", "full_name", new_column_name="name")
    op.add_column("users", sa.Column("surname", sa.String(255), nullable=True))
    op.add_column("users", sa.Column("company", sa.String(255), nullable=True))


def downgrade() -> None:
    op.execute(
        "UPDATE users SET name = LEFT(name || ' ' || surname, 255) "
        "WHERE surname IS NOT NULL AND surname <> ''"
    )
    op.drop_column("users", "company")
    op.drop_column("users", "surname")
    op.alter_column("users", "name", new_column_name="full_name")
