"""Add per-user processing queue.

Revision ID: queue_001
Revises: b7c8d9e0f1a2
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "queue_001"
down_revision: str | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE_STATUS_PREDICATE = (
    "status IN ("
    "'in_progress', "
    "'cancelling', "
    "'ocr_in_progress', "
    "'ocr_completed', "
    "'classifying', "
    "'validating'"
    ")"
)

TIMESTAMP_COLUMNS = (
    "queued_at",
    "started_at",
    "finished_at",
    "cancel_requested_at",
    "cancelled_at",
)


def upgrade() -> None:
    # Allow NULL until existing analyses have their owners backfilled.
    op.add_column(
        "document_analyses",
        sa.Column(
            "owner_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )

    op.add_column(
        "document_analyses",
        sa.Column("pipeline", sa.String(50), nullable=True),
    )

    op.add_column(
        "document_analyses",
        sa.Column(
            "pipeline_params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )

    for column_name in TIMESTAMP_COLUMNS:
        op.add_column(
            "document_analyses",
            sa.Column(
                column_name,
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )

    op.execute(
        sa.text(
            """
            UPDATE document_analyses AS analysis
            SET owner_id = document.owner_id
            FROM documents AS document
            WHERE analysis.document_id = document.id
            """
        )
    )

    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM document_analyses
                    WHERE owner_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Cannot migrate queue: some analyses have no owner';
                END IF;
            END;
            $$;
            """
        )
    )

    op.alter_column(
        "document_analyses",
        "owner_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.create_foreign_key(
        "fk_document_analyses_owner_id_users",
        "document_analyses",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.create_index(
        "ix_document_analyses_owner_id",
        "document_analyses",
        ["owner_id"],
    )

    op.create_index(
        "ix_document_analyses_owner_queue",
        "document_analyses",
        ["owner_id", "status", "queued_at", "id"],
    )

    # Stop rather than arbitrarily changing existing processing states.
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT owner_id
                    FROM document_analyses
                    WHERE status IN (
                        'in_progress',
                        'cancelling',
                        'ocr_in_progress',
                        'ocr_completed',
                        'classifying',
                        'validating'
                    )
                    GROUP BY owner_id
                    HAVING COUNT(*) > 1
                ) THEN
                    RAISE EXCEPTION
                        'Cannot migrate queue: an owner has multiple active analyses';
                END IF;
            END;
            $$;
            """
        )
    )

    op.create_index(
        "uq_document_analyses_active_owner",
        "document_analyses",
        ["owner_id"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_STATUS_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_document_analyses_active_owner",
        table_name="document_analyses",
    )
    op.drop_index(
        "ix_document_analyses_owner_queue",
        table_name="document_analyses",
    )
    op.drop_index(
        "ix_document_analyses_owner_id",
        table_name="document_analyses",
    )

    op.drop_constraint(
        "fk_document_analyses_owner_id_users",
        "document_analyses",
        type_="foreignkey",
    )

    for column_name in reversed(TIMESTAMP_COLUMNS):
        op.drop_column("document_analyses", column_name)

    op.drop_column("document_analyses", "pipeline_params")
    op.drop_column("document_analyses", "pipeline")
    op.drop_column("document_analyses", "owner_id")
