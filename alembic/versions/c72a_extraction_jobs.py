# D469 — extraction_jobs mutable lifecycle table for single-doc and batch
# extraction runs. 15 columns, CHECK constraints on job_kind and status,
# composite index on (status, created_at DESC). shard_pids JSONB ships in
# schema but is always NULL in 72a (multi-shard deferred to 72b).
#
# Invariant carve-out: Alembic head change.
# (1) Invariant: Alembic migration chain.
# (2) Carve-out: new c72a_extraction_jobs migration.
# (3) Authorization: D469 / chunk-72a-spec-v6-FINAL.md §4.

"""c72a: extraction_jobs table (D469)

Revision ID: c72a_extraction_jobs
Revises: c69a_drift_runs
Create Date: 2026-05-27
"""

from alembic import op

revision: str = "c72a_extraction_jobs"
down_revision: str = "c69a_drift_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE extraction_jobs (
            job_id         UUID PRIMARY KEY,
            job_kind       VARCHAR(32) CHECK (job_kind IN ('document', 'batch')),
            source_path    TEXT NOT NULL,
            status         VARCHAR(16) DEFAULT 'pending'
                           CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
            pid            INTEGER,
            progress_json  JSONB DEFAULT '{}'::jsonb,
            error_message  TEXT,
            started_at     TIMESTAMPTZ,
            completed_at   TIMESTAMPTZ,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            created_by     TEXT,
            provider       TEXT,
            model          TEXT,
            cost_budget_usd NUMERIC(10,4),
            shard_pids     JSONB
        )
    """)
    op.execute("""
        CREATE INDEX ix_extraction_jobs_status_created
        ON extraction_jobs (status, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_extraction_jobs_status_created")
    op.execute("DROP TABLE IF EXISTS extraction_jobs")
