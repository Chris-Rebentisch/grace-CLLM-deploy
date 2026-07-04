"""widen actor_type CHECK to include 'agent' (Chunk 46, D378.a / D364 fix-forward)

The c44a migration added agent_id / agent_display_name / delegation_source
columns but did not widen the CHECK constraint on actor_type.  This migration
completes the D364 contract so that ``actor_type='agent'`` INSERTs succeed.

Revision ID: c46a_d364_agent_identity
Revises: c45a_support_sessions
Create Date: 2026-05-12 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c46a_d364_agent_identity"
down_revision: Union[str, None] = "c45a_support_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE elicitation_events "
        "DROP CONSTRAINT ck_elicitation_events_actor_type"
    )
    op.execute(
        "ALTER TABLE elicitation_events "
        "ADD CONSTRAINT ck_elicitation_events_actor_type "
        "CHECK (actor_type IN ('human', 'system', 'agent'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE elicitation_events "
        "DROP CONSTRAINT ck_elicitation_events_actor_type"
    )
    op.execute(
        "ALTER TABLE elicitation_events "
        "ADD CONSTRAINT ck_elicitation_events_actor_type "
        "CHECK (actor_type IN ('human', 'system'))"
    )
