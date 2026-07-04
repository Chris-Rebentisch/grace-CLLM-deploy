"""create elicitation_sessions table

Chunk 29, D223. Elicitation session persistence with hybrid columnar+JSONB
pattern matching CQTestRunRow. Append-only trigger blocks DELETE and
immutable-column UPDATE; lifecycle fields are updatable.

Revision ID: c29a_elicitation_sessions
Revises: b4e7a291cd5f
Create Date: 2026-05-01 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c29a_elicitation_sessions"
down_revision: Union[str, Sequence[str], None] = "b4e7a291cd5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "elicitation_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_type",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "current_phase",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_plan_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "metadata_extra",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.CheckConstraint(
            "actor_type IN ('human', 'system')",
            name="ck_elicitation_sessions_actor_type",
        ),
        sa.CheckConstraint(
            "current_phase IN ('prepare', 'open', 'structure', 'clarify', 'close', 'none')",
            name="ck_elicitation_sessions_current_phase",
        ),
    )
    op.create_index(
        "ix_elicitation_sessions_actor_type",
        "elicitation_sessions",
        ["actor_type"],
    )
    op.create_index(
        "ix_elicitation_sessions_current_phase",
        "elicitation_sessions",
        ["current_phase"],
    )
    op.create_index(
        "ix_elicitation_sessions_started_at",
        "elicitation_sessions",
        ["started_at"],
    )

    # Append-only trigger: block DELETE entirely; block UPDATE on immutable
    # columns (session_id, actor_type, started_at). Lifecycle fields
    # (current_phase, paused_at, closed_at, session_plan_jsonb) are updatable.
    op.execute(
        """
CREATE OR REPLACE FUNCTION trig_elicitation_sessions_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'elicitation_sessions rows are append-only; DELETE forbidden';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF NEW.session_id IS DISTINCT FROM OLD.session_id
            OR NEW.actor_type IS DISTINCT FROM OLD.actor_type
            OR NEW.started_at IS DISTINCT FROM OLD.started_at THEN
            RAISE EXCEPTION 'elicitation_sessions immutable columns cannot be modified';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trig_elicitation_sessions_immutable
    BEFORE UPDATE OR DELETE ON elicitation_sessions
    FOR EACH ROW
    EXECUTE FUNCTION trig_elicitation_sessions_immutable();
"""
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trig_elicitation_sessions_immutable "
        "ON elicitation_sessions;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS trig_elicitation_sessions_immutable();"
    )
    op.drop_index(
        "ix_elicitation_sessions_started_at",
        table_name="elicitation_sessions",
    )
    op.drop_index(
        "ix_elicitation_sessions_current_phase",
        table_name="elicitation_sessions",
    )
    op.drop_index(
        "ix_elicitation_sessions_actor_type",
        table_name="elicitation_sessions",
    )
    op.drop_table("elicitation_sessions")
