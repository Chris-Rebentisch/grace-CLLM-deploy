"""create eval_runs and deepeval_results tables

Chunk 34, D259. DeepEval regression suite results persistence.

Creates:
  - eval_runs table — one row per CLI invocation or API-triggered eval run.
  - deepeval_results table — N rows per run, one per (case, metric) pair.
    Unique on (run_id, case_id, metric_name).
  - GRANT SELECT to grace_readonly (D167) on both tables.

Revision ID: c34_eval_runs_and_results
Revises: c33_correlations_and_alerts
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c34_eval_runs_and_results"
down_revision: Union[str, Sequence[str], None] = "c33_correlations_and_alerts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_METRIC_NAMES = (
    "contextual_precision",
    "contextual_recall",
    "faithfulness",
    "answer_relevancy",
    "hallucination",
)


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("golden_dataset_hash", sa.Text(), nullable=False),
        sa.Column(
            "total_cases", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "passed_warn_floor",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "passed_fail_floor",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('running','success','partial_failure','error')",
            name="ck_eval_runs_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('cli','api')",
            name="ck_eval_runs_triggered_by",
        ),
    )
    op.create_index(
        "ix_eval_runs_started_at",
        "eval_runs",
        [sa.text("started_at DESC")],
    )

    metric_check = (
        "metric_name IN ("
        + ",".join(f"'{m}'" for m in _METRIC_NAMES)
        + ")"
    )

    op.create_table(
        "deepeval_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("case_id", sa.Text(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("metric_name", sa.Text(), nullable=False),
        sa.Column("metric_score", sa.Float(), nullable=False),
        sa.Column("passed_warn_floor", sa.Boolean(), nullable=False),
        sa.Column("passed_fail_floor", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(metric_check, name="ck_deepeval_results_metric"),
        sa.CheckConstraint(
            "metric_score >= 0 AND metric_score <= 1",
            name="ck_deepeval_results_score_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["eval_runs.id"],
            name="fk_deepeval_results_run_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "case_id",
            "metric_name",
            name="uq_deepeval_results_run_case_metric",
        ),
    )
    op.create_index(
        "ix_deepeval_results_metric_evaluated_at",
        "deepeval_results",
        ["metric_name", sa.text("evaluated_at DESC")],
    )
    op.create_index(
        "ix_deepeval_results_run_evaluated_at",
        "deepeval_results",
        ["run_id", sa.text("evaluated_at DESC")],
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON eval_runs TO grace_readonly';
        EXECUTE 'GRANT SELECT ON deepeval_results TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deepeval_results_run_evaluated_at",
        table_name="deepeval_results",
    )
    op.drop_index(
        "ix_deepeval_results_metric_evaluated_at",
        table_name="deepeval_results",
    )
    op.drop_table("deepeval_results")
    op.drop_index("ix_eval_runs_started_at", table_name="eval_runs")
    op.drop_table("eval_runs")
