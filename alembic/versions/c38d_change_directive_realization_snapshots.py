"""create change_directive_realization_snapshots table

Chunk 38, D294. Created **empty** — Chunk 39 populates with
realization-tracking computation. Append-only via ``BEFORE UPDATE
OR DELETE`` trigger raising ``check_violation``. The table exists
in Chunk 38 so the schema is stable before population logic lands.

Revision ID: c38d_change_directive_realization_snapshots
Revises: c38c_change_directive_evidence_criteria
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c38d_cd_realization_snapshots"
down_revision: Union[str, Sequence[str], None] = "c38c_cd_evidence_criteria"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_directive_realization_snapshots",
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
        sa.Column(
            "snapshot_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column(
            "criteria_results",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("progress_percentage", sa.Numeric(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["directive_id"], ["change_directives.directive_id"]
        ),
    )

    op.create_index(
        "ix_change_directive_realization_snapshots_directive_at",
        "change_directive_realization_snapshots",
        ["directive_id", sa.text("snapshot_at DESC")],
    )

    op.execute(
        """
CREATE OR REPLACE FUNCTION change_directive_realization_snapshots_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'change_directive_realization_snapshots is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_change_directive_realization_snapshots_append_only
BEFORE UPDATE OR DELETE ON change_directive_realization_snapshots
FOR EACH ROW EXECUTE FUNCTION change_directive_realization_snapshots_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON change_directive_realization_snapshots TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_change_directive_realization_snapshots_append_only "
        "ON change_directive_realization_snapshots"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS change_directive_realization_snapshots_append_only()"
    )
    op.drop_index(
        "ix_change_directive_realization_snapshots_directive_at",
        table_name="change_directive_realization_snapshots",
    )
    op.drop_table("change_directive_realization_snapshots")
