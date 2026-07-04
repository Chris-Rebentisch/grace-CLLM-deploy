"""widen decomposition_runs for Layer 5 + Layer 6 (Chunk 41, D327)

Additive ALTER on ``decomposition_runs``:

1. Add ``layer5_decision JSONB NULL`` and ``layer6_validation
   JSONB NULL``. Both are NULLable; first-write-only semantics
   are enforced by the ``decomposition_runs_append_only()`` trigger
   function (extended below to cover the two new columns).

2. Widen the status CHECK constraint from 4 values
   (``running``, ``completed``, ``failed``, ``paused_pre_layer4``)
   to 7 — adding ``paused_pre_layer5``, ``paused_pre_layer6``,
   ``paused_pre_layer7``. ``paused_pre_layer4`` is kept (real
   failure mode with resume semantics; outline Q7 resolved).

3. ``CREATE OR REPLACE FUNCTION decomposition_runs_append_only()``
   with the **full** function body — including the original four
   JSONB guards (``layer1_summary``, ``layer2_decision``,
   ``layer3_decision``, ``layer4_hypotheses`` per
   ``c40a_decomposition_runs.py:177–201``) PLUS two new guards
   for ``layer5_decision`` and ``layer6_validation`` with
   identical ``IS DISTINCT FROM ... AND OLD.{col} IS NOT NULL``
   logic. Simply adding columns without updating the function
   would leave them unprotected (spec §3.3 / §18 gap #1).

Revision ID: c41b_decomposition_runs_layer5_layer6
Revises: c41a_segmentation_maps
Create Date: 2026-05-08 12:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c41b_runs_l5_l6"
down_revision: Union[str, Sequence[str], None] = "c41a_segmentation_maps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_STATUS_CHECK = (
    "status IN ("
    "'running','completed','failed',"
    "'paused_pre_layer4',"
    "'paused_pre_layer5','paused_pre_layer6','paused_pre_layer7')"
)
_OLD_STATUS_CHECK = (
    "status IN ('running','completed','failed','paused_pre_layer4')"
)


def upgrade() -> None:
    op.add_column(
        "decomposition_runs",
        sa.Column(
            "layer5_decision",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "decomposition_runs",
        sa.Column(
            "layer6_validation",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    op.drop_constraint(
        "ck_decomposition_runs_status",
        "decomposition_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_decomposition_runs_status",
        "decomposition_runs",
        _NEW_STATUS_CHECK,
    )

    # CREATE OR REPLACE — extends the c40a trigger function body to
    # cover the two new JSONB columns. Full body required because the
    # function enumerates JSONB columns explicitly.
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
    IF NEW.layer5_decision IS DISTINCT FROM OLD.layer5_decision
       AND OLD.layer5_decision IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.layer5_decision is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.layer6_validation IS DISTINCT FROM OLD.layer6_validation
       AND OLD.layer6_validation IS NOT NULL THEN
        RAISE EXCEPTION
            'decomposition_runs.layer6_validation is first-write-only'
            USING ERRCODE = 'check_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")

    # Restore the c40a function body (4 JSONB guards only).
    op.execute(
        """
CREATE OR REPLACE FUNCTION decomposition_runs_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'decomposition_runs is append-only; DELETE denied'
            USING ERRCODE = 'check_violation';
    END IF;

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

    op.drop_constraint(
        "ck_decomposition_runs_status",
        "decomposition_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_decomposition_runs_status",
        "decomposition_runs",
        _OLD_STATUS_CHECK,
    )

    op.drop_column("decomposition_runs", "layer6_validation")
    op.drop_column("decomposition_runs", "layer5_decision")
