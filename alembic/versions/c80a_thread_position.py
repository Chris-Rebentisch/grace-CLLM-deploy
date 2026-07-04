"""Add thread_position to communication_events + trigger carve-out for thread reconstruction (Chunk 80a, D513).

DDL target — ALTER ``communication_events``:
  ADD ``thread_position INT NULL``.
  ``CREATE OR REPLACE FUNCTION trg_communication_events_guard()`` — removes
  ``thread_id`` and ``thread_orphan`` from the 19-column immutable blocklist
  (reduced to 17) so the RFC 5322 thread reconstructor can overwrite both.
  ``thread_position`` is mutable by absence from the blocklist.

D356 capture-the-why: invariant = c56a append-only trigger; carve-out =
D513 — ``thread_position`` additive column + trigger carve-out making
``thread_id``/``thread_orphan`` mutable for RFC 5322 thread reconstruction;
mirrors c59a/c79a ``CREATE OR REPLACE`` carve-out. Authorization: D513.

Revision ID: c80a_thread_position
Revises: c79a_comm_event_extract
Create Date: 2026-05-29
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c80a_thread_position"
down_revision: Union[str, Sequence[str], None] = "c79a_comm_event_extract"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# -- Trigger: communication_events guard (c56a/c59a/c79a extension) --
# Invariant: communication_events is append-only with mutable carve-out.
# Carve-out: D513 removes thread_id and thread_orphan from the 19-column
# immutable blocklist (now 17 columns). The reconstructor overwrites both
# (thread_id: adapter-provisional → RFC 5322 root; thread_orphan: false → true).
# thread_position is mutable by absence from the blocklist.
# Authorization: D513.

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
    -- 17 immutable columns must be unchanged (D513: thread_id, thread_orphan removed from blocklist)
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
       OR (NEW.raw_headers_json IS DISTINCT FROM OLD.raw_headers_json)
    THEN
      RAISE EXCEPTION
        'communication_events: only triage_tier_outcome, sensitivity_tags, observed_in_sources_json, retriage_cycle, retriage_state, extraction_status, extraction_event_id, extracted_at, thread_id, thread_orphan, thread_position are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# Previous trigger body (c79a version) — restored verbatim on downgrade.
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
        'communication_events: only triage_tier_outcome, sensitivity_tags, observed_in_sources_json, retriage_cycle, retriage_state, extraction_status, extraction_event_id, extracted_at are mutable'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # Add thread_position column
    op.add_column(
        "communication_events",
        sa.Column("thread_position", sa.Integer(), nullable=True),
    )

    # Replace trigger function — 17-column blocklist (thread_id, thread_orphan removed)
    op.execute(_CE_TRIGGER_FN_REPLACE)


def downgrade() -> None:
    # Revert trigger to c79a version (19-column blocklist)
    op.execute(_CE_TRIGGER_FN_PREVIOUS)

    # Drop thread_position column
    op.drop_column("communication_events", "thread_position")
