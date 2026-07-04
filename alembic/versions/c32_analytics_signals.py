"""create analytics_signals and signal_runs tables

Chunk 32, D240, D247. Signal computation pipeline persistence layer.

Creates:
  - signal_type Postgres enum (7 values: A, B, C, D, E, F,
    trust_miscalibration). The seventh enum value is reserved per D247
    for future detector module work; no detector ships in Chunk 32.
  - signal_runs table — one row per orchestrator invocation.
  - analytics_signals table — N rows per run, one per emitted signal
    record. Unique on (run_id, signal_type, ontology_module) to
    enforce idempotency.
  - GRANT SELECT to grace_readonly (D167) on both tables.

Revision ID: c32_analytics_signals
Revises: c30_human_decided_at
Create Date: 2026-05-04 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# NOTE: alembic_version.version_num is VARCHAR(32); revision id must fit.
revision: str = "c32_analytics_signals"
down_revision: Union[str, Sequence[str], None] = "c30_human_decided_at"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_SIGNAL_TYPE_VALUES = (
    "A",
    "B",
    "C",
    "D",
    "E",
    "F",
    "trust_miscalibration",
)


def upgrade() -> None:
    # Create the enum first; SQLAlchemy will not auto-create it via the
    # table column because we set create_type=False below.
    signal_type_enum = postgresql.ENUM(
        *_SIGNAL_TYPE_VALUES,
        name="signal_type",
        create_type=False,
    )
    signal_type_enum.create(op.get_bind(), checkfirst=False)

    op.create_table(
        "signal_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('running','success','partial_failure','error')",
            name="ck_signal_runs_status",
        ),
        sa.CheckConstraint(
            "triggered_by IN ('cli')",
            name="ck_signal_runs_triggered_by",
        ),
    )

    op.create_table(
        "analytics_signals",
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
        sa.Column(
            "signal_type",
            postgresql.ENUM(
                *_SIGNAL_TYPE_VALUES,
                name="signal_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "ontology_module",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'__global__'"),
        ),
        sa.Column("strength", sa.Float(), nullable=False),
        sa.Column(
            "evidence_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "strength >= 0 AND strength <= 1",
            name="ck_analytics_signals_strength_bounds",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["signal_runs.id"],
            name="fk_analytics_signals_run_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "run_id",
            "signal_type",
            "ontology_module",
            name="uq_analytics_signals_run_signal_module",
        ),
    )
    op.create_index(
        "ix_analytics_signals_signal_type_detected_at",
        "analytics_signals",
        ["signal_type", sa.text("detected_at DESC")],
    )
    op.create_index(
        "ix_analytics_signals_ontology_module_detected_at",
        "analytics_signals",
        ["ontology_module", sa.text("detected_at DESC")],
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    # Grant SELECT only. Role creation is operator-managed (see
    # docs/security-posture.md) — guard with a DO block so this is a
    # no-op when the role does not yet exist (e.g. CI envs).
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON signal_runs TO grace_readonly';
        EXECUTE 'GRANT SELECT ON analytics_signals TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    # FK from analytics_signals.run_id is ON DELETE CASCADE; dropping
    # analytics_signals first removes its own FK definition cleanly.
    op.drop_index(
        "ix_analytics_signals_ontology_module_detected_at",
        table_name="analytics_signals",
    )
    op.drop_index(
        "ix_analytics_signals_signal_type_detected_at",
        table_name="analytics_signals",
    )
    op.drop_table("analytics_signals")
    op.drop_table("signal_runs")

    signal_type_enum = postgresql.ENUM(
        *_SIGNAL_TYPE_VALUES,
        name="signal_type",
        create_type=False,
    )
    signal_type_enum.drop(op.get_bind(), checkfirst=False)
