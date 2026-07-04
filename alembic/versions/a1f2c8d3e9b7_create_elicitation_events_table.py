"""create elicitation_events table

Chunk 27, D195 / protocol §8.3. Append-only table for session telemetry.
Trigger blocks UPDATE and DELETE on every column — audit trail is
immutable at the database level.

Revision ID: a1f2c8d3e9b7
Revises: e7f9c8d25a1b
Create Date: 2026-04-22 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1f2c8d3e9b7"
down_revision: Union[str, Sequence[str], None] = "e7f9c8d25a1b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "elicitation_events",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("phase_name", sa.String(length=16), nullable=False),
        sa.Column(
            "emitted_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("grace_version", sa.String(length=32), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "payload_schema_version", sa.Integer(), nullable=False
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.CheckConstraint(
            "actor_type IN ('human', 'pass')",
            name="ck_elicitation_events_actor_type",
        ),
    )
    op.create_index(
        "ix_elicitation_events_session_id",
        "elicitation_events",
        ["session_id"],
    )
    op.create_index(
        "ix_elicitation_events_phase_name",
        "elicitation_events",
        ["phase_name"],
    )
    op.create_index(
        "ix_elicitation_events_event_type",
        "elicitation_events",
        ["event_type"],
    )

    op.execute(
        """
CREATE OR REPLACE FUNCTION prevent_elicitation_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'elicitation_events is append-only. Deletes are not permitted.';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION 'elicitation_events is append-only. Updates are not permitted.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trig_elicitation_events_immutable
    BEFORE UPDATE OR DELETE ON elicitation_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_elicitation_event_mutation();
"""
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trig_elicitation_events_immutable "
        "ON elicitation_events;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS prevent_elicitation_event_mutation();"
    )
    op.drop_index(
        "ix_elicitation_events_event_type", table_name="elicitation_events"
    )
    op.drop_index(
        "ix_elicitation_events_phase_name", table_name="elicitation_events"
    )
    op.drop_index(
        "ix_elicitation_events_session_id", table_name="elicitation_events"
    )
    op.drop_table("elicitation_events")
