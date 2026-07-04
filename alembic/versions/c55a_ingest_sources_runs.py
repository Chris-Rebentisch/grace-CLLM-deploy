"""create ingestion_sources and ingestion_runs tables (Chunk 55, D419/D420/D427)

Two tables:

1. ``ingestion_sources`` — mutable source registry with soft-delete.
2. ``ingestion_runs`` — append-only with mutable lifecycle carve-out.
   DELETE blocked; UPDATE only on lifecycle columns (status, completed_at,
   checkpoint_json, error_text, triage_tier_counts_json).

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c55a_ingest_sources_runs
Revises: c53a_connector_infra
Create Date: 2026-05-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c55a_ingest_sources_runs"
down_revision: Union[str, Sequence[str], None] = "c53a_connector_infra"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Trigger SQL for ingestion_runs (append-only with lifecycle carve-out) --
# Invariant: ingestion_runs is append-only.
# Carve-out: UPDATE permitted on lifecycle columns (status, completed_at,
#            checkpoint_json, error_text, triage_tier_counts_json).
# Authorization: spec §6 Step 6, §8.2.

_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION trg_ingestion_runs_guard()
RETURNS TRIGGER AS $$
BEGIN
  -- Allow alembic downgrade to bypass
  IF current_setting('alembic.downgrading', true) = 'true' THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    ELSE
      RETURN NEW;
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'ingestion_runs is append-only: DELETE forbidden'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    -- Immutable columns: id, source_id, started_at
    IF (NEW.id IS DISTINCT FROM OLD.id)
       OR (NEW.source_id IS DISTINCT FROM OLD.source_id)
       OR (NEW.started_at IS DISTINCT FROM OLD.started_at)
    THEN
      RAISE EXCEPTION
        'ingestion_runs: only status, completed_at, checkpoint_json, error_text, triage_tier_counts_json are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_ATTACH = """\
CREATE TRIGGER trg_ingestion_runs_guard
  BEFORE UPDATE OR DELETE ON ingestion_runs
  FOR EACH ROW EXECUTE FUNCTION trg_ingestion_runs_guard();
"""

_GRANT_READONLY = """\
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
    GRANT SELECT ON ingestion_sources TO grace_readonly;
    GRANT SELECT ON ingestion_runs TO grace_readonly;
  END IF;
END
$$;
"""


def upgrade() -> None:
    # --- Table 1: ingestion_sources (mutable) ---
    op.create_table(
        "ingestion_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.TEXT(), nullable=False),
        sa.Column("source_type", sa.TEXT(), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("segment", sa.TEXT(), nullable=False),
        sa.Column(
            "enabled",
            sa.BOOLEAN(),
            nullable=False,
            server_default="true",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "deleted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_ingestion_sources_name"),
    )

    # Partial indexes on source_type and segment filtered WHERE deleted_at IS NULL
    op.create_index(
        "ix_ingestion_sources_source_type",
        "ingestion_sources",
        ["source_type"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_ingestion_sources_segment",
        "ingestion_sources",
        ["segment"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # --- Table 2: ingestion_runs (append-only with lifecycle carve-out) ---
    op.create_table(
        "ingestion_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "completed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.TEXT(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "checkpoint_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_text", sa.TEXT(), nullable=True),
        sa.Column(
            "triage_tier_counts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ingestion_sources.id"]
        ),
    )

    op.create_index("ix_ingestion_runs_source_id", "ingestion_runs", ["source_id"])
    op.create_index("ix_ingestion_runs_status", "ingestion_runs", ["status"])
    op.create_index(
        "ix_ingestion_runs_started_at",
        "ingestion_runs",
        [sa.text("started_at DESC")],
    )

    # Append-only trigger
    op.execute(_TRIGGER_FN)
    op.execute(_TRIGGER_ATTACH)

    # GRANT SELECT to grace_readonly
    op.execute(_GRANT_READONLY)


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.drop_table("ingestion_runs")
    op.drop_table("ingestion_sources")
    op.execute("DROP FUNCTION IF EXISTS trg_ingestion_runs_guard() CASCADE")
