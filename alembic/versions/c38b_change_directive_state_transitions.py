"""create change_directive_state_transitions table

Chunk 38, D294. Append-only audit trail for Change_Directive status
transitions. Each row is hash-chained to the previous transition row
for the same ``directive_id`` (pattern from
``src/ontology/schema_store.py:181-194``).

Append-only at the database level via ``BEFORE UPDATE OR DELETE``
trigger raising ``ERRCODE='check_violation'``. The trigger honours
the ``alembic.downgrading`` setting so downgrades can drop the
table without bypassing application-level enforcement.

Revision ID: c38b_change_directive_state_transitions
Revises: c38a_change_directives
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c38b_cd_state_transitions"
down_revision: Union[str, Sequence[str], None] = "c38a_change_directives"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_directive_state_transitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "directive_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("from_state", sa.Text(), nullable=False),
        sa.Column("to_state", sa.Text(), nullable=False),
        sa.Column(
            "superseded_by_directive_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "transitioned_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "transitioned_by",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("hash_chain", sa.Text(), nullable=False),
        sa.Column("prev_transition_hash", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["directive_id"], ["change_directives.directive_id"]
        ),
    )

    op.create_index(
        "ix_change_directive_state_transitions_directive_at",
        "change_directive_state_transitions",
        ["directive_id", sa.text("transitioned_at DESC")],
    )

    # Append-only trigger: BEFORE UPDATE OR DELETE raises check_violation
    # except when the alembic.downgrading session setting is true.
    op.execute(
        """
CREATE OR REPLACE FUNCTION change_directive_transitions_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'change_directive_state_transitions is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_change_directive_transitions_append_only
BEFORE UPDATE OR DELETE ON change_directive_state_transitions
FOR EACH ROW EXECUTE FUNCTION change_directive_transitions_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON change_directive_state_transitions TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_change_directive_transitions_append_only "
        "ON change_directive_state_transitions"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS change_directive_transitions_append_only()"
    )
    op.drop_index(
        "ix_change_directive_state_transitions_directive_at",
        table_name="change_directive_state_transitions",
    )
    op.drop_table("change_directive_state_transitions")
