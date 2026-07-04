"""create connector infrastructure tables (Chunk 53, D413)

Two tables:

1. ``entity_resolution_review_queue`` — append-only queue for entity
   resolution outcomes below the high-confidence floor (D410). DELETE
   raises ``check_violation``; UPDATE restricted to ``status``,
   ``reviewed_by``, ``reviewed_at`` columns only.

2. ``connector_sync_state`` — mutable sync-state tracking per namespace.
   Upserted via INSERT ... ON CONFLICT (namespace_id) DO UPDATE. No
   append-only trigger.

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c53a_connector_infra
Revises: c51c_ontology_scope
Create Date: 2026-05-15 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c53a_connector_infra"
down_revision: Union[str, Sequence[str], None] = "c51c_ontology_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# -- Trigger SQL for entity_resolution_review_queue (append-only) ----------

_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION trg_er_review_queue_guard()
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
    RAISE EXCEPTION 'entity_resolution_review_queue is append-only: DELETE forbidden'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    -- Only status, reviewed_by, reviewed_at may be updated.
    IF (NEW.id IS DISTINCT FROM OLD.id)
       OR (NEW.namespace_id IS DISTINCT FROM OLD.namespace_id)
       OR (NEW.source_record_id IS DISTINCT FROM OLD.source_record_id)
       OR (NEW.entity_type IS DISTINCT FROM OLD.entity_type)
       OR (NEW.record_payload IS DISTINCT FROM OLD.record_payload)
       OR (NEW.proposed_canonical_grace_id IS DISTINCT FROM OLD.proposed_canonical_grace_id)
       OR (NEW.resolution_method IS DISTINCT FROM OLD.resolution_method)
       OR (NEW.created_at IS DISTINCT FROM OLD.created_at)
    THEN
      RAISE EXCEPTION
        'entity_resolution_review_queue: only status, reviewed_by, reviewed_at are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER_ATTACH = """\
CREATE TRIGGER trg_er_review_queue_guard
  BEFORE UPDATE OR DELETE ON entity_resolution_review_queue
  FOR EACH ROW EXECUTE FUNCTION trg_er_review_queue_guard();
"""

_GRANT_READONLY = """\
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
    GRANT SELECT ON entity_resolution_review_queue TO grace_readonly;
    GRANT SELECT ON connector_sync_state TO grace_readonly;
  END IF;
END
$$;
"""


def upgrade() -> None:
    # --- Table 1: entity_resolution_review_queue (append-only) ---
    op.create_table(
        "entity_resolution_review_queue",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "namespace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("source_record_id", sa.VARCHAR(), nullable=False),
        sa.Column("entity_type", sa.VARCHAR(), nullable=False),
        sa.Column(
            "record_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "proposed_canonical_grace_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("resolution_method", sa.VARCHAR(), nullable=True),
        sa.Column(
            "status",
            sa.VARCHAR(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_by", sa.VARCHAR(), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["graph_namespaces.id"]
        ),
    )

    # Append-only trigger
    op.execute(_TRIGGER_FN)
    op.execute(_TRIGGER_ATTACH)

    # --- Table 2: connector_sync_state (mutable) ---
    op.create_table(
        "connector_sync_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "namespace_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("connector_type", sa.VARCHAR(), nullable=False),
        sa.Column("schema_hash", sa.VARCHAR(), nullable=True),
        sa.Column("record_count", sa.INTEGER(), server_default="0"),
        sa.Column("last_error", sa.TEXT(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["namespace_id"], ["graph_namespaces.id"]
        ),
        sa.UniqueConstraint("namespace_id", name="uq_connector_sync_state_ns"),
    )

    # GRANT SELECT to grace_readonly
    op.execute(_GRANT_READONLY)


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.drop_table("connector_sync_state")
    op.drop_table("entity_resolution_review_queue")
    op.execute("DROP FUNCTION IF EXISTS trg_er_review_queue_guard() CASCADE")
