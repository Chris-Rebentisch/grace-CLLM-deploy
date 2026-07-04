"""Add extraction_status, extraction_event_id, extracted_at to communication_events (Chunk 79, D512).

DDL target — ALTER ``communication_events``:
  ADD ``extraction_status TEXT DEFAULT 'pending'`` with CHECK constraint
  (pending, extracted, failed, skipped).
  ADD ``extraction_event_id UUID NULL``.
  ADD ``extracted_at TIMESTAMPTZ NULL``.
  ``CREATE OR REPLACE FUNCTION trg_communication_events_guard()`` — keeps the
  c56a 19-column immutable blocklist byte-identical; only updates the RAISE
  EXCEPTION message to document the 8 mutable columns.  extraction_status,
  extraction_event_id, and extracted_at are mutable by *absence* from the
  blocklist (c56a/c59a pattern).

D356 capture-the-why: invariant = c56a append-only trigger; carve-out =
D512 trigger extension for extraction_status lifecycle columns (mutable by
absence from 19-column blocklist); authorization = D512.

Revision ID: c79a_comm_event_extract
Revises: c78a_voice_card_exports
Create Date: 2026-05-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c79a_comm_event_extract"
down_revision: Union[str, Sequence[str], None] = "c78a_voice_card_exports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Trigger: communication_events guard (c56a/c59a extension) --
# Invariant: communication_events is append-only with mutable carve-out.
# Carve-out: D512 adds extraction_status + extraction_event_id + extracted_at
#            as mutable (absent from the 19-column immutable blocklist —
#            mutable by absence, c56a/c59a pattern).
# The 19-column blocklist is byte-identical to c56a.  Only the RAISE
# EXCEPTION message string is updated to document the expanded mutable set.
# Authorization: D512.

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
        'communication_events: only triage_tier_outcome, sensitivity_tags, observed_in_sources_json, retriage_cycle, retriage_state, extraction_status, extraction_event_id, extracted_at are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Previous trigger body (c59a version) — restored verbatim on downgrade.
_CE_TRIGGER_FN_PREVIOUS = """\
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


def upgrade() -> None:
    # Add extraction columns
    op.add_column(
        "communication_events",
        sa.Column(
            "extraction_status",
            sa.TEXT(),
            nullable=True,
            server_default="pending",
        ),
    )
    op.execute(
        "ALTER TABLE communication_events "
        "ADD CONSTRAINT ck_communication_events_extraction_status "
        "CHECK (extraction_status IN ('pending', 'extracted', 'failed', 'skipped'))"
    )
    op.add_column(
        "communication_events",
        sa.Column("extraction_event_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "communication_events",
        sa.Column("extracted_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Replace trigger function — 19-column blocklist unchanged; error message
    # updated to document the 8 mutable columns.
    op.execute(_CE_TRIGGER_FN_REPLACE)


def downgrade() -> None:
    # Revert trigger to c59a version
    op.execute(_CE_TRIGGER_FN_PREVIOUS)

    # Drop extraction columns
    op.execute(
        "ALTER TABLE communication_events "
        "DROP CONSTRAINT IF EXISTS ck_communication_events_extraction_status"
    )
    op.drop_column("communication_events", "extracted_at")
    op.drop_column("communication_events", "extraction_event_id")
    op.drop_column("communication_events", "extraction_status")
