"""create gap_reports table

Chunk 36, D280. Persists Perception-Evidence Gap Reports keyed by
review_session_id. Append-only (force-regenerate produces a new row;
reads return the most recent by generated_at DESC).

Creates:
  - gap_reports table — additive; no destructive ALTER.
  - GRANT SELECT to grace_readonly (D167).

Revision ID: c36a_gap_reports
Revises: c35a_retrieval_feedback
Create Date: 2026-05-06 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c36a_gap_reports"
down_revision: Union[str, Sequence[str], None] = "c35a_retrieval_feedback"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "gap_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("erd_score", sa.Float(), nullable=False),
        sa.Column("erd_threshold_n", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_extra",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["review_sessions.id"],
            name="fk_gap_reports_session",
        ),
    )
    op.create_index(
        "ix_gap_reports_session_id",
        "gap_reports",
        ["session_id"],
    )
    op.create_index(
        "ix_gap_reports_generated_at",
        "gap_reports",
        [sa.text("generated_at DESC")],
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON gap_reports TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index("ix_gap_reports_generated_at", table_name="gap_reports")
    op.drop_index("ix_gap_reports_session_id", table_name="gap_reports")
    op.drop_table("gap_reports")
