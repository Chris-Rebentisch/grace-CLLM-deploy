"""add reconciliation columns to review_sessions

Chunk 36, D280. Adds three additive columns (gap_report_id FK, erd_score,
erd_threshold_n) plus an index on gap_report_id. No backfill of pre-Phase-5.5
sessions per D275 audit-anchor policy.

Revision ID: c36b_review_sessions_recon_columns
Revises: c36a_gap_reports
Create Date: 2026-05-06 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c36b_review_sessions_recon"
down_revision: Union[str, Sequence[str], None] = "c36a_gap_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_sessions",
        sa.Column(
            "gap_report_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "review_sessions",
        sa.Column("erd_score", sa.Float(), nullable=True),
    )
    op.add_column(
        "review_sessions",
        sa.Column("erd_threshold_n", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_review_sessions_gap_report",
        "review_sessions",
        "gap_reports",
        ["gap_report_id"],
        ["id"],
    )
    op.create_index(
        "ix_review_sessions_gap_report_id",
        "review_sessions",
        ["gap_report_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_sessions_gap_report_id",
        table_name="review_sessions",
    )
    op.drop_constraint(
        "fk_review_sessions_gap_report",
        "review_sessions",
        type_="foreignkey",
    )
    op.drop_column("review_sessions", "erd_threshold_n")
    op.drop_column("review_sessions", "erd_score")
    op.drop_column("review_sessions", "gap_report_id")
