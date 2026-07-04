"""create permission_hypothesis_runs table (Chunk 42, D331/D310 mirror)

Append-only audit trail of role-cluster hypothesis generation runs.
Mirrors the Chunk 40 ``decomposition_runs`` pattern: row is INSERTed
at start; payload columns first-write-only via JSONB trigger.

Columns:

* ``run_id`` — surrogate UUID PK.
* ``evidence_id`` — UUID identifying the EvidenceBundle this run
  consumed.
* ``status`` — ``running`` / ``completed`` / ``failed``.
* ``hypothesis_set`` — JSONB ``RoleClusterHypothesisSet`` payload
  (first-write-only).
* ``operator`` — operator handle (optional).
* ``created_at`` / ``completed_at``.

First-write-only JSONB trigger: ``hypothesis_set`` may be UPDATEd only
when the OLD value is NULL.

Append-only on identity columns (``run_id``, ``evidence_id``,
``created_at``); status / completed_at may be UPDATEd freely.

Revision ID: c42c_permission_hypothesis_runs
Revises: c42b_permission_drift_queue
Create Date: 2026-05-08 16:02:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c42c_permission_hypothesis_runs"
down_revision: Union[str, Sequence[str], None] = "c42b_permission_drift_queue"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "permission_hypothesis_runs",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default="running",
        ),
        sa.Column(
            "hypothesis_set",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("operator", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "completed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.CheckConstraint(
            "status IN ('running','completed','failed')",
            name="ck_permission_hypothesis_runs_status",
        ),
    )

    op.create_index(
        "ix_permission_hypothesis_runs_evidence",
        "permission_hypothesis_runs",
        ["evidence_id", sa.text("created_at DESC")],
    )

    op.execute(
        """
CREATE OR REPLACE FUNCTION permission_hypothesis_runs_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'permission_hypothesis_runs is append-only (DELETE denied)'
            USING ERRCODE = 'check_violation';
    END IF;
    -- Identity columns may not change.
    IF NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.evidence_id IS DISTINCT FROM OLD.evidence_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'permission_hypothesis_runs identity columns are immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    -- hypothesis_set is first-write-only.
    IF OLD.hypothesis_set IS NOT NULL
       AND NEW.hypothesis_set IS DISTINCT FROM OLD.hypothesis_set THEN
        RAISE EXCEPTION
            'permission_hypothesis_runs.hypothesis_set is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_permission_hypothesis_runs_append_only
BEFORE UPDATE OR DELETE ON permission_hypothesis_runs
FOR EACH ROW EXECUTE FUNCTION permission_hypothesis_runs_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON permission_hypothesis_runs TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_permission_hypothesis_runs_append_only "
        "ON permission_hypothesis_runs"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS permission_hypothesis_runs_append_only()"
    )
    op.drop_index(
        "ix_permission_hypothesis_runs_evidence",
        table_name="permission_hypothesis_runs",
    )
    op.drop_table("permission_hypothesis_runs")
