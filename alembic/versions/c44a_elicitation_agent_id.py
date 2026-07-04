"""add agent identity columns to elicitation_events (Chunk 44, D364)

Additive ALTER TABLE: three nullable columns for agent identity
propagation on the existing ``elicitation_events`` table. No new
tables, triggers, or indexes at v1 volume.

Revision ID: c44a_elicitation_agent_id
Revises: c43a_sensitivity_reports
Create Date: 2026-05-11 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c44a_elicitation_agent_id"
down_revision: Union[str, None] = "c43a_sensitivity_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "elicitation_events",
        sa.Column("agent_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "elicitation_events",
        sa.Column("agent_display_name", sa.Text(), nullable=True),
    )
    op.add_column(
        "elicitation_events",
        sa.Column(
            "delegation_source",
            sa.Text(),
            sa.CheckConstraint(
                "delegation_source IN ('user_direct', 'agent_on_behalf', 'system_scheduled')",
                name="ck_elicitation_events_delegation_source",
            ),
            nullable=True,
        ),
    )

    # D167 — ensure grace_readonly can SELECT on the table.
    # Conditional: role may not exist in dev environments.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
                EXECUTE 'GRANT SELECT ON elicitation_events TO grace_readonly';
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_column("elicitation_events", "delegation_source")
    op.drop_column("elicitation_events", "agent_display_name")
    op.drop_column("elicitation_events", "agent_id")
