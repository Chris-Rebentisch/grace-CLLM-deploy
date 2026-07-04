"""create communication_events and curated_email_subsets tables (Chunk 56, D435)

Two tables:

1. ``communication_events`` — append-only with mutable carve-out on
   ``triage_tier_outcome``, ``sensitivity_tags``, ``observed_in_sources_json``.
   DELETE blocked; UPDATE only when 19 immutable columns unchanged.
2. ``curated_email_subsets`` — append-only with ``sentinel_status`` carve-out.

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c56a_communication_events
Revises: c55a_ingest_sources_runs
Create Date: 2026-05-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c56a_communication_events"
down_revision: Union[str, Sequence[str], None] = "c55a_ingest_sources_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Trigger: communication_events guard --
# Invariant: communication_events is append-only.
# Carve-out: UPDATE permitted on triage_tier_outcome, sensitivity_tags,
#            observed_in_sources_json.
# Authorization: D435, spec §8.1.

_CE_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION trg_communication_events_guard()
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
    RAISE EXCEPTION 'communication_events is append-only: DELETE forbidden'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    -- 19 immutable columns must be unchanged
    IF (NEW.id IS DISTINCT FROM OLD.id)
       OR (NEW.message_id IS DISTINCT FROM OLD.message_id)
       OR (NEW.sender_email IS DISTINCT FROM OLD.sender_email)
       OR (NEW.sender_display_name IS DISTINCT FROM OLD.sender_display_name)
       OR (NEW.recipients_json IS DISTINCT FROM OLD.recipients_json)
       OR (NEW.subject IS DISTINCT FROM OLD.subject)
       OR (NEW.body_plain IS DISTINCT FROM OLD.body_plain)
       OR (NEW.body_html IS DISTINCT FROM OLD.body_html)
       OR (NEW.sent_at IS DISTINCT FROM OLD.sent_at)
       OR (NEW.received_at IS DISTINCT FROM OLD.received_at)
       OR (NEW.ingested_at IS DISTINCT FROM OLD.ingested_at)
       OR (NEW.source_id IS DISTINCT FROM OLD.source_id)
       OR (NEW.ontology_module IS DISTINCT FROM OLD.ontology_module)
       OR (NEW.attachments_json IS DISTINCT FROM OLD.attachments_json)
       OR (NEW.in_reply_to IS DISTINCT FROM OLD.in_reply_to)
       OR (NEW.references_json IS DISTINCT FROM OLD.references_json)
       OR (NEW.thread_id IS DISTINCT FROM OLD.thread_id)
       OR (NEW.thread_orphan IS DISTINCT FROM OLD.thread_orphan)
       OR (NEW.raw_headers_json IS DISTINCT FROM OLD.raw_headers_json)
    THEN
      RAISE EXCEPTION
        'communication_events: only triage_tier_outcome, sensitivity_tags, observed_in_sources_json are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CE_TRIGGER_ATTACH = """\
CREATE TRIGGER trg_communication_events_guard
  BEFORE UPDATE OR DELETE ON communication_events
  FOR EACH ROW EXECUTE FUNCTION trg_communication_events_guard();
"""


# -- Trigger: curated_email_subsets guard --
# Invariant: curated_email_subsets is append-only.
# Carve-out: UPDATE permitted on sentinel_status only.
# Authorization: D435, spec §8.2.

_CES_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION trg_curated_email_subsets_guard()
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
    RAISE EXCEPTION 'curated_email_subsets is append-only: DELETE forbidden'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    -- All columns except sentinel_status must be unchanged
    IF (NEW.id IS DISTINCT FROM OLD.id)
       OR (NEW.source_id IS DISTINCT FROM OLD.source_id)
       OR (NEW.deployment_path IS DISTINCT FROM OLD.deployment_path)
       OR (NEW.selected_message_ids IS DISTINCT FROM OLD.selected_message_ids)
       OR (NEW.diversity_metrics IS DISTINCT FROM OLD.diversity_metrics)
       OR (NEW.created_by IS DISTINCT FROM OLD.created_by)
       OR (NEW.created_at IS DISTINCT FROM OLD.created_at)
    THEN
      RAISE EXCEPTION
        'curated_email_subsets: only sentinel_status is mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CES_TRIGGER_ATTACH = """\
CREATE TRIGGER trg_curated_email_subsets_guard
  BEFORE UPDATE OR DELETE ON curated_email_subsets
  FOR EACH ROW EXECUTE FUNCTION trg_curated_email_subsets_guard();
"""

_GRANT_READONLY = """\
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
    GRANT SELECT ON communication_events TO grace_readonly;
    GRANT SELECT ON curated_email_subsets TO grace_readonly;
  END IF;
END
$$;
"""


def upgrade() -> None:
    # --- Table 1: communication_events ---
    op.create_table(
        "communication_events",
        # -- 19 immutable columns --
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("message_id", sa.TEXT(), nullable=False),
        sa.Column("sender_email", sa.TEXT(), nullable=False),
        sa.Column("sender_display_name", sa.TEXT(), nullable=True),
        sa.Column(
            "recipients_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("subject", sa.TEXT(), nullable=True),
        sa.Column("body_plain", sa.TEXT(), nullable=True),
        sa.Column("body_html", sa.TEXT(), nullable=True),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "ingested_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("ontology_module", sa.TEXT(), nullable=True),
        sa.Column(
            "attachments_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("in_reply_to", sa.TEXT(), nullable=True),
        sa.Column(
            "references_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("thread_id", sa.TEXT(), nullable=True),
        sa.Column(
            "thread_orphan",
            sa.BOOLEAN(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "raw_headers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        # -- 3 mutable columns --
        sa.Column(
            "triage_tier_outcome",
            sa.TEXT(),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("sensitivity_tags", sa.TEXT(), nullable=True),
        sa.Column(
            "observed_in_sources_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ingestion_sources.id"]
        ),
    )

    # Indexes
    op.create_index(
        "ix_communication_events_source_id",
        "communication_events",
        ["source_id"],
    )
    op.create_index(
        "ix_communication_events_triage_outcome",
        "communication_events",
        ["triage_tier_outcome"],
    )
    op.create_index(
        "ix_communication_events_sent_at",
        "communication_events",
        [sa.text("sent_at DESC")],
    )
    op.create_index(
        "ix_communication_events_ontology_module",
        "communication_events",
        ["ontology_module"],
    )
    # Composite index (B8 audit-round recommendation)
    op.create_index(
        "ix_communication_events_src_triage_id",
        "communication_events",
        ["source_id", "triage_tier_outcome", "id"],
    )
    # Partial unique index for dedup
    op.create_index(
        "uq_communication_events_msgid_source",
        "communication_events",
        ["message_id", "source_id"],
        unique=True,
        postgresql_where=sa.text("message_id IS NOT NULL"),
    )

    # Trigger
    op.execute(_CE_TRIGGER_FN)
    op.execute(_CE_TRIGGER_ATTACH)

    # --- Table 2: curated_email_subsets ---
    op.create_table(
        "curated_email_subsets",
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
        sa.Column("deployment_path", sa.TEXT(), nullable=False),
        sa.Column(
            "selected_message_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "diversity_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_by", sa.TEXT(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "sentinel_status",
            sa.TEXT(),
            nullable=False,
            server_default="pending",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["source_id"], ["ingestion_sources.id"]
        ),
        sa.CheckConstraint(
            "sentinel_status IN ('pending', 'ready', 'consumed')",
            name="ck_curated_email_subsets_sentinel_status",
        ),
    )

    # Trigger
    op.execute(_CES_TRIGGER_FN)
    op.execute(_CES_TRIGGER_ATTACH)

    # GRANT SELECT to grace_readonly
    op.execute(_GRANT_READONLY)


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.drop_table("curated_email_subsets")
    op.drop_table("communication_events")
    op.execute("DROP FUNCTION IF EXISTS trg_communication_events_guard() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_curated_email_subsets_guard() CASCADE")
