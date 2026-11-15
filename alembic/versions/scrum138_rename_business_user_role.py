"""Rename the business_user role to user.

Revision ID: scrum138_user_role
Revises: scrum136_user_name
"""

from collections.abc import Sequence

from alembic import op

revision: str = "scrum138_user_role"
down_revision: str | None = "scrum136_user_name"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("UPDATE users SET role = 'user' WHERE role = 'business_user'")
    op.alter_column("users", "role", server_default="user")


def downgrade() -> None:
    op.execute("UPDATE users SET role = 'business_user' WHERE role = 'user'")
    op.alter_column("users", "role", server_default="business_user")
