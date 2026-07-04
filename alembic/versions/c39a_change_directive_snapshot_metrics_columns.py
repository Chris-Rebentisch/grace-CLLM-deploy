"""Add realization snapshot metric columns (Chunk 39, D300).

Revision ID: c39a_cd_snapshot_metrics
Revises: c38d_cd_realization_snapshots
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c39a_cd_snapshot_metrics"
down_revision: Union[str, Sequence[str], None] = "c38d_cd_realization_snapshots"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "change_directive_realization_snapshots",
        sa.Column("velocity", sa.Numeric(), nullable=True),
    )
    op.add_column(
        "change_directive_realization_snapshots",
        sa.Column("evidence_count_consistent", sa.Integer(), nullable=True),
    )
    op.add_column(
        "change_directive_realization_snapshots",
        sa.Column("evidence_count_counter", sa.Integer(), nullable=True),
    )
    op.add_column(
        "change_directive_realization_snapshots",
        sa.Column("first_evidence_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "change_directive_realization_snapshots",
        sa.Column(
            "last_counter_evidence_seen_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "change_directive_realization_snapshots",
        sa.Column("criteria_all_satisfied", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(
        "change_directive_realization_snapshots", "criteria_all_satisfied"
    )
    op.drop_column(
        "change_directive_realization_snapshots",
        "last_counter_evidence_seen_at",
    )
    op.drop_column(
        "change_directive_realization_snapshots", "first_evidence_seen_at"
    )
    op.drop_column(
        "change_directive_realization_snapshots", "evidence_count_counter"
    )
    op.drop_column(
        "change_directive_realization_snapshots", "evidence_count_consistent"
    )
    op.drop_column("change_directive_realization_snapshots", "velocity")
