"""create decomposition_runs table

Chunk 40, D310. New table backing the Organizational Decomposition
pipeline (Layers 1–4). The row is INSERTed at start with
``status='running'`` and NULL JSONB cells; ``finalize_run()`` lands
all four layer artifacts in a single UPDATE at lifecycle close.

Append-only trigger semantics:

* DELETE is always denied.
* UPDATE on identity / provenance columns (``run_id``,
  ``archive_root``, ``archive_root_canonical_hash``, ``started_at``,
  ``created_at``, ``operator``, ``resumed_from_run_id``,
  ``total_documents``) is denied.
* UPDATE on lifecycle columns (``status``, ``completed_at``) is
  always allowed.
* UPDATE on JSONB columns (``layer1_summary``, ``layer2_decision``,
  ``layer3_decision``, ``layer4_hypotheses``) is allowed only when
  the OLD value IS NULL — first-write-only semantics that preserve
  D310's append-only intent (JSONB values are written once and never
  overwritten).

The trigger honours an ``alembic.downgrading`` GUC escape valve so
``alembic downgrade`` round-trips cleanly.

Revision ID: c40a_decomposition_runs
Revises: c39a_cd_snapshot_metrics
Create Date: 2026-05-07 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c40a_decomposition_runs"
down_revision: Union[str, Sequence[str], None] = "c39a_cd_snapshot_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "decomposition_runs",
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("archive_root", sa.Text(), nullable=False),
        sa.Column(
            "archive_root_canonical_hash", sa.CHAR(length=64), nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("total_documents", sa.Integer(), nullable=True),
        sa.Column(
            "operator", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "resumed_from_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "layer1_summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "layer2_decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "layer3_decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "layer4_hypotheses",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("run_id"),
        sa.ForeignKeyConstraint(
            ["resumed_from_run_id"], ["decomposition_runs.run_id"]
        ),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','paused_pre_layer4')",
            name="ck_decomposition_runs_status",
        ),
    )

    op.create_index(
        "ix_decomposition_runs_archive_status_started",
        "decomposition_runs",
        ["archive_root_canonical_hash", "status", sa.text("started_at DESC")],
    )

    # Append-only trigger: see module docstring for full semantics.
    op.execute(
        """
CREATE OR REPLACE FUNCTION decomposition_runs_append_only()
RETURNS TRIGGER AS $$
BEGIN
    -- Allow Alembic downgrades to drop / mutate freely.
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'decomposition_runs is append-only; DELETE denied'
            USING ERRCODE = 'check_violation';
    END IF;

    -- UPDATE: identity / provenance columns are immutable.
    IF NEW.run_id IS DISTINCT FROM OLD.run_id THEN
        RAISE EXCEPTION
            'decomposition_runs.run_id is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.archive_root IS DISTINCT FROM OLD.archive_root THEN
        RAISE EXCEPTION
            'decomposition_runs.archive_root is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.archive_root_canonical_hash
        IS DISTINCT FROM OLD.archive_root_canonical_hash THEN
        RAISE EXCEPTION
            'decomposition_runs.archive_root_canonical_hash is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.started_at IS DISTINCT FROM OLD.started_at THEN
        RAISE EXCEPTION
            'decomposition_runs.started_at is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION
            'decomposition_runs.created_at is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.operator IS DISTINCT FROM OLD.operator THEN
        RAISE EXCEPTION
            'decomposition_runs.operator is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.resumed_from_run_id IS DISTINCT FROM OLD.resumed_from_run_id THEN
        RAISE EXCEPTION
            'decomposition_runs.resumed_from_run_id is immutable'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.total_documents IS DISTINCT FROM OLD.total_documents
       AND OLD.total_documents IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.total_documents is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;

    -- JSONB columns: first-write-only (NULL -> value allowed; overwrite denied).
    IF NEW.layer1_summary IS DISTINCT FROM OLD.layer1_summary
       AND OLD.layer1_summary IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.layer1_summary is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.layer2_decision IS DISTINCT FROM OLD.layer2_decision
       AND OLD.layer2_decision IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.layer2_decision is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.layer3_decision IS DISTINCT FROM OLD.layer3_decision
       AND OLD.layer3_decision IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.layer3_decision is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.layer4_hypotheses IS DISTINCT FROM OLD.layer4_hypotheses
       AND OLD.layer4_hypotheses IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.layer4_hypotheses is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )

    op.execute(
        """
CREATE TRIGGER trg_decomposition_runs_append_only
BEFORE UPDATE OR DELETE ON decomposition_runs
FOR EACH ROW EXECUTE FUNCTION decomposition_runs_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON decomposition_runs TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_decomposition_runs_append_only "
        "ON decomposition_runs"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS decomposition_runs_append_only()"
    )
    op.drop_index(
        "ix_decomposition_runs_archive_status_started",
        table_name="decomposition_runs",
    )
    op.drop_table("decomposition_runs")
