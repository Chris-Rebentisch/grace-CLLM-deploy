"""create permission_drift_queue table (Chunk 42, D337)

Mutable queue of pending drift classifications produced by the kNN
drift detector. Operator decision flips the row from ``pending`` to
``decided``.

Columns:

* ``drift_queue_id`` — surrogate UUID PK.
* ``person_grace_id`` — graph identifier of the Person whose
  membership is being classified.
* ``proposed_cluster_id`` — pre-filled best-guess cluster.
* ``drift_band`` — one of ``high`` / ``medium`` / ``low`` per D337.
* ``status`` — ``pending`` / ``decided`` / ``ignored``.
* ``operator_decision`` — operator-recorded cluster (NULLable while
  pending).
* ``rationale`` — operator-recorded free text (optional).
* ``created_at`` / ``decided_at``.

Mutability: rows are mutated by the operator decision route. There
is no append-only trigger here — by design D337 specifies mutable
classification rows.

Revision ID: c42b_permission_drift_queue
Revises: c42a_permission_matrices
Create Date: 2026-05-08 16:01:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c42b_permission_drift_queue"
down_revision: Union[str, Sequence[str], None] = "c42a_permission_matrices"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "permission_drift_queue",
        sa.Column(
            "drift_queue_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("person_grace_id", sa.Text(), nullable=False),
        sa.Column("proposed_cluster_id", sa.Text(), nullable=True),
        sa.Column("drift_band", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("operator_decision", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "decided_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.PrimaryKeyConstraint("drift_queue_id"),
        sa.CheckConstraint(
            "drift_band IN ('high','medium','low')",
            name="ck_permission_drift_queue_band",
        ),
        sa.CheckConstraint(
            "status IN ('pending','decided','ignored')",
            name="ck_permission_drift_queue_status",
        ),
    )

    op.create_index(
        "ix_permission_drift_queue_status",
        "permission_drift_queue",
        ["status", sa.text("created_at DESC")],
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON permission_drift_queue TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_permission_drift_queue_status",
        table_name="permission_drift_queue",
    )
    op.drop_table("permission_drift_queue")
