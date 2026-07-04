"""create retrieval_feedback table

Chunk 35a, D266. Persists thumbs-up / thumbs-down feedback for
retrieval responses, scoped to a query event identifier so the
signal pipeline can later correlate feedback with the underlying
retrieval strategy mix.

Creates:
  - retrieval_feedback table — append-only feedback rows.
  - GRANT SELECT to grace_readonly (D167).

Revision ID: c35a_retrieval_feedback
Revises: c34_eval_runs_and_results
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c35a_retrieval_feedback"
down_revision: Union[str, Sequence[str], None] = "c34_eval_runs_and_results"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "retrieval_feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("query_event_id", sa.Text(), nullable=False),
        sa.Column("vote", sa.Text(), nullable=False),
        sa.Column("freetext", sa.Text(), nullable=True),
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "vote IN ('up','down')",
            name="ck_retrieval_feedback_vote",
        ),
        sa.CheckConstraint(
            "freetext IS NULL OR char_length(freetext) <= 2048",
            name="ck_retrieval_feedback_freetext_len",
        ),
    )
    op.create_index(
        "ix_retrieval_feedback_query_event_id",
        "retrieval_feedback",
        ["query_event_id"],
    )
    op.create_index(
        "ix_retrieval_feedback_submitted_at",
        "retrieval_feedback",
        [sa.text("submitted_at DESC")],
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON retrieval_feedback TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_retrieval_feedback_submitted_at",
        table_name="retrieval_feedback",
    )
    op.drop_index(
        "ix_retrieval_feedback_query_event_id",
        table_name="retrieval_feedback",
    )
    op.drop_table("retrieval_feedback")
