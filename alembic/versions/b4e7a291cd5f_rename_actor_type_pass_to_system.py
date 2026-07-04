"""rename actor_type 'pass' to 'system' in elicitation_events

Observation 1 ratification (2026-04-23): 'pass' was semantically odd
--- the spec note was "Chunk 27 always emits `human`", so 'pass' was
the reserved slot for autonomous emission. 'system' is clearer.
No production code path ever wrote 'pass', so no data migration is
needed; this is a pure CHECK-constraint rename.

Revision ID: b4e7a291cd5f
Revises: a1f2c8d3e9b7
Create Date: 2026-04-23

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e7a291cd5f"
down_revision: Union[str, Sequence[str], None] = "a1f2c8d3e9b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_elicitation_events_actor_type",
        "elicitation_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_elicitation_events_actor_type",
        "elicitation_events",
        "actor_type IN ('human', 'system')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_elicitation_events_actor_type",
        "elicitation_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_elicitation_events_actor_type",
        "elicitation_events",
        "actor_type IN ('human', 'pass')",
    )
