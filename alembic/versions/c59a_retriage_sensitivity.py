"""Re-triage scheduler columns + sensitivity propagation table (Chunk 59, D441)

DDL target 1 — ALTER ``communication_events``:
  ADD ``retriage_cycle`` INT NULL, ``retriage_state`` TEXT NULL.
  ``CREATE OR REPLACE FUNCTION trg_communication_events_guard()`` — keeps the
  c56a 19-column immutable blocklist byte-identical; only updates the RAISE
  EXCEPTION message to document the two new mutable columns.  retriage_cycle
  and retriage_state are mutable by *absence* from the blocklist (D421, c56a
  trigger pattern).  Partial index on (retriage_cycle, retriage_state).

DDL target 2 — ALTER ``gap_reports``:
  ADD ``mixed_source_coverage`` BOOLEAN NOT NULL DEFAULT FALSE.  Partial index.

DDL target 3 — CREATE ``communication_sensitivity_propagation``:
  Append-only-with-mutable-columns trigger.  DELETE forbidden except during
  alembic downgrade.  UPDATE permitted only on ``propagated_tags`` +
  ``last_recomputed_at``.  Authorization: D426, D440.

``GRANT SELECT`` on ``communication_sensitivity_propagation`` to
``grace_readonly`` (D167).

Revision ID: c59a_retriage_sensitivity
Revises: c58a_voice_tone_profiles
Create Date: 2026-05-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c59a_retriage_sensitivity"
down_revision: Union[str, Sequence[str], None] = "c58a_voice_tone_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Trigger: communication_events guard (c56a extension) --
# Invariant: communication_events is append-only with mutable carve-out.
# Carve-out: D421 adds retriage_cycle + retriage_state as mutable (absent
#            from the 19-column immutable blocklist — mutable by absence).
# The 19-column blocklist is byte-identical to c56a.  Only the RAISE
# EXCEPTION message string is updated to document the expanded mutable set.
# D356 capture-the-why: invariant = c56a append-only trigger; carve-out =
# new mutable columns via absence; authorization = D421 + D441.

_CE_TRIGGER_FN_REPLACE = """\
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
        'communication_events: only triage_tier_outcome, sensitivity_tags, observed_in_sources_json, retriage_cycle, retriage_state are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Original c56a trigger body — restored verbatim on downgrade.
_CE_TRIGGER_FN_ORIGINAL = """\
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


# -- Trigger: communication_sensitivity_propagation guard --
# Invariant: append-only with mutable-columns carve-out.
# Carve-out: UPDATE permitted on propagated_tags + last_recomputed_at only.
# DELETE forbidden except during alembic downgrade (D435 §303 precedent).
# Authorization: D426, D440.

_CSP_TRIGGER_FN = """\
CREATE OR REPLACE FUNCTION trg_communication_sensitivity_propagation_guard()
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
    RAISE EXCEPTION 'communication_sensitivity_propagation is append-only: DELETE forbidden'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    -- thread_id and propagated_at are immutable
    IF (NEW.thread_id IS DISTINCT FROM OLD.thread_id)
       OR (NEW.propagated_at IS DISTINCT FROM OLD.propagated_at)
    THEN
      RAISE EXCEPTION
        'communication_sensitivity_propagation: only propagated_tags, last_recomputed_at are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_CSP_TRIGGER_ATTACH = """\
CREATE TRIGGER trg_communication_sensitivity_propagation_guard
  BEFORE UPDATE OR DELETE ON communication_sensitivity_propagation
  FOR EACH ROW EXECUTE FUNCTION trg_communication_sensitivity_propagation_guard();
"""

_GRANT_READONLY = """\
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
    GRANT SELECT ON communication_sensitivity_propagation TO grace_readonly;
  END IF;
END
$$;
"""


def upgrade() -> None:
    # --- DDL target 1: ALTER communication_events ---
    op.add_column(
        "communication_events",
        sa.Column("retriage_cycle", sa.INTEGER(), nullable=True),
    )
    op.add_column(
        "communication_events",
        sa.Column("retriage_state", sa.TEXT(), nullable=True),
    )

    # Replace trigger function — 19-column blocklist unchanged; error message
    # updated to document the 5 mutable columns.
    op.execute(_CE_TRIGGER_FN_REPLACE)

    # Partial index for retriage worklist query
    op.create_index(
        "ix_communication_events_retriage_cycle_state",
        "communication_events",
        ["retriage_cycle", "retriage_state"],
        postgresql_where=sa.text("retriage_state IS DISTINCT FROM 'passed'"),
    )

    # --- DDL target 2: ALTER gap_reports ---
    op.add_column(
        "gap_reports",
        sa.Column(
            "mixed_source_coverage",
            sa.BOOLEAN(),
            nullable=False,
            server_default="false",
        ),
    )
    op.create_index(
        "ix_gap_reports_mixed_source_coverage",
        "gap_reports",
        ["mixed_source_coverage"],
        postgresql_where=sa.text("mixed_source_coverage = TRUE"),
    )

    # --- DDL target 3: CREATE communication_sensitivity_propagation ---
    op.create_table(
        "communication_sensitivity_propagation",
        sa.Column("thread_id", sa.TEXT(), nullable=False),
        sa.Column("propagated_tags", sa.TEXT(), nullable=True),
        sa.Column(
            "propagated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "last_recomputed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.PrimaryKeyConstraint("thread_id"),
    )

    # Trigger + GRANT
    op.execute(_CSP_TRIGGER_FN)
    op.execute(_CSP_TRIGGER_ATTACH)
    op.execute(_GRANT_READONLY)


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")

    # --- Reverse DDL target 3 ---
    op.drop_table("communication_sensitivity_propagation")
    op.execute(
        "DROP FUNCTION IF EXISTS trg_communication_sensitivity_propagation_guard() CASCADE"
    )

    # --- Reverse DDL target 2 ---
    op.drop_index("ix_gap_reports_mixed_source_coverage", table_name="gap_reports")
    op.drop_column("gap_reports", "mixed_source_coverage")

    # --- Reverse DDL target 1 ---
    op.drop_index(
        "ix_communication_events_retriage_cycle_state",
        table_name="communication_events",
    )
    op.drop_column("communication_events", "retriage_state")
    op.drop_column("communication_events", "retriage_cycle")

    # Restore original c56a trigger body verbatim
    op.execute(_CE_TRIGGER_FN_ORIGINAL)
