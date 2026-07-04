# D460 — drift_runs table for correlating drift-detection runs across
# API/CLI paths. Mutable (no append-only trigger). Upstream context:
# D333 drift_detector, D337 matrix_repository.

"""c69a: drift_runs table (D460)

Revision ID: c69a_drift_runs
Revises: c65b_schema_proposals_correction
Create Date: 2026-05-27
"""

from alembic import op

revision: str = "c69a_drift_runs"
down_revision: str = "c65b_schema_proposals_correction"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE drift_runs (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            run_id           UUID NOT NULL,
            observation_time TIMESTAMPTZ NULL,
            dry_run          BOOLEAN NOT NULL DEFAULT false,
            started_at       TIMESTAMPTZ NOT NULL,
            completed_at     TIMESTAMPTZ NULL,
            status           TEXT NOT NULL CHECK (status IN ('running','success','partial_failure','error')),
            triggered_by     TEXT NOT NULL CHECK (triggered_by IN ('cli','api')),
            summary_json     JSONB NOT NULL DEFAULT '{}',
            error_message    TEXT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS drift_runs")
