"""create support_sessions table (Chunk 45, D372)

Append-only governance table for remote support sessions with
immutable/mutable column split.

Immutable columns (UPDATE raises ``check_violation``): ``id``,
``granted_by_user_id``, ``granted_to_email``, ``granted_at``,
``expires_at``, ``scope_tags``, ``created_via``, ``token_hash``.

Mutable columns (UPDATE allowed): ``revoked_at``, ``revoke_reason``,
``last_used_at``.

DELETE is blocked unconditionally.

Partial unique index ``uix_support_sessions_active`` enforces at-most-
one active (non-revoked) session at the database level.

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c45a_support_sessions
Revises: c44a_elicitation_agent_id
Create Date: 2026-05-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c45a_support_sessions"
down_revision: Union[str, Sequence[str], None] = "c44a_elicitation_agent_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "support_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("granted_by_user_id", sa.Text(), nullable=False),
        sa.Column("granted_to_email", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("revoke_reason", sa.Text(), nullable=True),
        sa.Column(
            "scope_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{\"all\": true}'::jsonb"),
        ),
        sa.Column("created_via", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_support_sessions_token_hash"),
        sa.CheckConstraint(
            "created_via IN ('api', 'cli')",
            name="ck_support_sessions_created_via",
        ),
    )

    # Partial unique index: at most one non-revoked session at any time.
    # Uses a constant expression ((1)) since PostgreSQL rejects NOW() in
    # index predicates (STABLE, not IMMUTABLE). Application-level expiry
    # check adds the time dimension.
    op.execute(
        """
CREATE UNIQUE INDEX uix_support_sessions_active
ON support_sessions ((1))
WHERE revoked_at IS NULL;
"""
    )

    op.create_index(
        "ix_support_sessions_granted_at",
        "support_sessions",
        [sa.text("granted_at DESC")],
    )

    # Append-only trigger with immutable/mutable column split (D372).
    # DELETE blocked unconditionally. UPDATE restricted to mutable columns
    # (revoked_at, revoke_reason, last_used_at); attempts to modify
    # immutable columns raise check_violation.
    # Trigger honours alembic.downgrading GUC for downgrade round-trips
    # (D291/D326 pattern).
    op.execute(
        """
CREATE OR REPLACE FUNCTION support_sessions_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;

    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'support_sessions does not allow DELETE'
            USING ERRCODE = 'check_violation';
    END IF;

    -- UPDATE: check immutable columns.
    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.granted_by_user_id IS DISTINCT FROM OLD.granted_by_user_id
           OR NEW.granted_to_email IS DISTINCT FROM OLD.granted_to_email
           OR NEW.granted_at IS DISTINCT FROM OLD.granted_at
           OR NEW.expires_at IS DISTINCT FROM OLD.expires_at
           OR NEW.scope_tags IS DISTINCT FROM OLD.scope_tags
           OR NEW.created_via IS DISTINCT FROM OLD.created_via
           OR NEW.token_hash IS DISTINCT FROM OLD.token_hash
        THEN
            RAISE EXCEPTION
                'support_sessions: cannot modify immutable columns'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_support_sessions_append_only
BEFORE UPDATE OR DELETE ON support_sessions
FOR EACH ROW EXECUTE FUNCTION support_sessions_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON support_sessions TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_support_sessions_append_only "
        "ON support_sessions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS support_sessions_append_only()"
    )
    op.drop_index(
        "ix_support_sessions_granted_at",
        table_name="support_sessions",
    )
    op.drop_index(
        "uix_support_sessions_active",
        table_name="support_sessions",
    )
    op.drop_table("support_sessions")
