"""create ingestion_checkpoints table + ingestion_sources.status column (Chunk 57, D424/D425)

Two DDL targets:

1. ``ingestion_checkpoints`` — mutable single-row-per-source state table for
   incremental sync cursors. NOT append-only (intentional departure from
   Chunk 36+ append-only pattern).
   Invariant departed: Chunk 36+ append-only pattern.
   Carve-out: mutable state table for incremental sync cursors.
   Authorization: D424/D425.

2. ``ingestion_sources.status`` — persistent source-level lifecycle.
   c55a left lifecycle implicit; OAuth callback and adapter-error policy
   require persistent source-level lifecycle.

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c57a_ingest_chk_apscheduler
Revises: c56a_communication_events
Create Date: 2026-05-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c57a_ingest_chk_apscheduler"
down_revision: Union[str, Sequence[str], None] = "c56a_communication_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # DDL target 1: ingestion_checkpoints table
    op.create_table(
        "ingestion_checkpoints",
        sa.Column("source_id", sa.dialects.postgresql.UUID(as_uuid=True), sa.ForeignKey("ingestion_sources.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("checkpoint_type", sa.String(20), nullable=False),
        sa.Column("checkpoint_value", sa.Text(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "checkpoint_type IN ('file_offset', 'uid_validity', 'delta_link', 'history_id')",
            name="ck_ingestion_checkpoints_checkpoint_type",
        ),
    )

    # D167: read-only role grant (conditional — role may not exist in dev)
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
            GRANT SELECT ON ingestion_checkpoints TO grace_readonly;
          END IF;
        END
        $$;
        """
    )

    # DDL target 2: ingestion_sources.status column
    op.add_column(
        "ingestion_sources",
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
    )

    # Backfill: archive or completed-run sources → 'ready'
    op.execute(
        """
        UPDATE ingestion_sources
           SET status = 'ready'
         WHERE source_type IN ('mbox', 'eml', 'msg', 'pst')
            OR id IN (SELECT DISTINCT source_id FROM ingestion_runs WHERE status = 'completed')
        """
    )


def downgrade() -> None:
    op.drop_table("ingestion_checkpoints")
    op.drop_column("ingestion_sources", "status")
