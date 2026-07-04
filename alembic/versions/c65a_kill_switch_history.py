"""Chunk 65 — kill_switch_history table (D447).

Append-only governance table for kill-switch engage/disengage cycles.
Records per-tier state snapshots, reason, and paired elicitation event ID.

Partial unique index ``uix_kill_switch_history_active`` enforces at-most-
one open engage row at the database level (same pattern as
``c45a_support_sessions.py:91–96``).

Immutable columns (UPDATE raises ``check_violation``): ``id``,
``engaged_at``, ``engaged_by``, ``reason``, ``previous_state``.

Mutable columns (UPDATE allowed): ``disengaged_at``, ``restored_state``,
``related_elicitation_event_id``.

DELETE is blocked unconditionally.

Revision ID: c65a_kill_switch_history
Revises: c59a_retriage_sensitivity
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c65a_kill_switch_history"
down_revision: Union[str, Sequence[str], None] = "c59a_retriage_sensitivity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kill_switch_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "engaged_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("disengaged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("engaged_by", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("previous_state", postgresql.JSONB(), nullable=False),
        sa.Column("restored_state", postgresql.JSONB(), nullable=True),
        sa.Column(
            "related_elicitation_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Partial unique index: at most one open (non-disengaged) engage row.
    # Uses a constant expression ((1)) since PostgreSQL rejects NOW() in
    # index predicates (STABLE, not IMMUTABLE). Pattern from c45a_support_sessions.py:91–96.
    op.execute(
        """
CREATE UNIQUE INDEX uix_kill_switch_history_active
ON kill_switch_history ((1))
WHERE disengaged_at IS NULL;
"""
    )

    # Append-only trigger with immutable/mutable column split (D447).
    # DELETE blocked unconditionally. UPDATE restricted to mutable columns
    # (disengaged_at, restored_state, related_elicitation_event_id);
    # attempts to modify immutable columns raise check_violation.
    op.execute("""
        CREATE OR REPLACE FUNCTION kill_switch_history_append_only()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF current_setting('alembic.downgrading', true) = 'true' THEN
                    RETURN OLD;
                END IF;
                RAISE EXCEPTION 'kill_switch_history is append-only: DELETE is forbidden'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                -- Immutable columns: raise on any change.
                IF OLD.id IS DISTINCT FROM NEW.id
                   OR OLD.engaged_at IS DISTINCT FROM NEW.engaged_at
                   OR OLD.engaged_by IS DISTINCT FROM NEW.engaged_by
                   OR OLD.reason IS DISTINCT FROM NEW.reason
                   OR OLD.previous_state IS DISTINCT FROM NEW.previous_state
                THEN
                    RAISE EXCEPTION 'kill_switch_history: immutable columns cannot be updated'
                        USING ERRCODE = 'check_violation';
                END IF;
                -- Mutable columns permitted by design:
                --   disengaged_at, restored_state, related_elicitation_event_id
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_kill_switch_history_append_only
        BEFORE INSERT OR UPDATE OR DELETE ON kill_switch_history
        FOR EACH ROW
        EXECUTE FUNCTION kill_switch_history_append_only();
    """)

    # D167: grace_readonly may not exist until bootstrap runs after upgrade
    # (CI order: alembic upgrade head → bootstrap). Match peer migrations:
    # conditional GRANT only when the role is present.
    op.execute("""
DO $g$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON kill_switch_history TO grace_readonly';
    END IF;
END
$g$;
""")


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true';")
    op.drop_table("kill_switch_history")
    op.execute("DROP FUNCTION IF EXISTS kill_switch_history_append_only();")
