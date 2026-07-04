"""create correlation_runs, diagnostic_records, and alert_events tables

Chunk 33, D248/D249. Cross-module correlation engine and alerting plane.

Creates:
  - correlation_runs table — one row per orchestrator invocation.
  - diagnostic_records table — N rows per run, one per emitted pattern
    record. Unique on (run_id, pattern_name, ontology_module).
  - alert_events table — one row per Grafana fire/resolve webhook.
  - GRANT SELECT to grace_readonly (D167) on all three tables.

Revision ID: c33_correlations_and_alerts
Revises: c32_analytics_signals
Create Date: 2026-05-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c33_correlations_and_alerts"
down_revision: Union[str, Sequence[str], None] = "c32_analytics_signals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PATTERN_NAMES = (
    "extraction_quality_problem",
    "graph_or_index_problem",
    "schema_drift_per_module",
    "cq_regression_pre_extraction",
    "relationship_gap_propagation",
)

_ROOT_CAUSE_MODULES = (
    "extraction",
    "retrieval",
    "graph",
    "ontology",
    "discovery",
)


def upgrade() -> None:
    op.create_table(
        "correlation_runs",
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
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('running','success','partial_failure','error')",
            name="ck_correlation_runs_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('cli')",
            name="ck_correlation_runs_triggered_by",
        ),
    )

    pattern_check = "pattern_name IN (" + ",".join(
        f"'{p}'" for p in _PATTERN_NAMES
    ) + ")"
    cause_check = "suspected_root_cause_module IN (" + ",".join(
        f"'{m}'" for m in _ROOT_CAUSE_MODULES
    ) + ")"

    op.create_table(
        "diagnostic_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("pattern_name", sa.Text(), nullable=False),
        sa.Column(
            "ontology_module",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'__global__'"),
        ),
        sa.Column(
            "suspected_root_cause_module", sa.Text(), nullable=False
        ),
        sa.Column("correlation_strength", sa.Float(), nullable=False),
        sa.Column(
            "contributing_signals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "evidence_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("human_summary", sa.Text(), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(pattern_check, name="ck_diagnostic_records_pattern"),
        sa.CheckConstraint(cause_check, name="ck_diagnostic_records_cause"),
        sa.CheckConstraint(
            "correlation_strength >= 0 AND correlation_strength <= 1",
            name="ck_diagnostic_records_strength_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["correlation_runs.id"],
            name="fk_diagnostic_records_run_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "pattern_name",
            "ontology_module",
            name="uq_diagnostic_records_run_pattern_module",
        ),
    )
    op.create_index(
        "ix_diagnostic_records_pattern_detected_at",
        "diagnostic_records",
        ["pattern_name", sa.text("detected_at DESC")],
    )
    op.create_index(
        "ix_diagnostic_records_cause_detected_at",
        "diagnostic_records",
        ["suspected_root_cause_module", sa.text("detected_at DESC")],
    )

    op.create_table(
        "alert_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("alertname", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("ontology_module", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "annotations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("webhook_payload_hash", sa.Text(), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "severity IN ('warning','critical')",
            name="ck_alert_events_severity",
        ),
        sa.CheckConstraint(
            "state IN ('firing','resolved')",
            name="ck_alert_events_state",
        ),
    )
    op.create_index(
        "ix_alert_events_alertname_fired_at",
        "alert_events",
        ["alertname", sa.text("fired_at DESC")],
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON correlation_runs TO grace_readonly';
        EXECUTE 'GRANT SELECT ON diagnostic_records TO grace_readonly';
        EXECUTE 'GRANT SELECT ON alert_events TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alert_events_alertname_fired_at", table_name="alert_events"
    )
    op.drop_table("alert_events")
    op.drop_index(
        "ix_diagnostic_records_cause_detected_at",
        table_name="diagnostic_records",
    )
    op.drop_index(
        "ix_diagnostic_records_pattern_detected_at",
        table_name="diagnostic_records",
    )
    op.drop_table("diagnostic_records")
    op.drop_table("correlation_runs")
