"""create change_directive_evidence_criteria table

Chunk 38, D294. EvidenceCriterion rows for Strategic_Initiative
directives. Mutable only when the parent directive is in ``draft``
status — a ``BEFORE UPDATE`` trigger reads the parent ``status`` and
raises ``check_violation`` otherwise. CHECK constraint enforces
``compilation_status = 'proposed' OR compiled_query IS NOT NULL``.

Revision ID: c38c_change_directive_evidence_criteria
Revises: c38b_change_directive_state_transitions
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c38c_cd_evidence_criteria"
down_revision: Union[str, Sequence[str], None] = "c38b_cd_state_transitions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_directive_evidence_criteria",
        sa.Column(
            "criterion_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "directive_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("natural_language", sa.Text(), nullable=False),
        sa.Column("measurement_kind", sa.Text(), nullable=True),
        sa.Column("target_value", sa.Text(), nullable=True),
        sa.Column("target_satisfied_when", sa.Text(), nullable=True),
        sa.Column("compiled_query", sa.Text(), nullable=True),
        sa.Column(
            "compilation_status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'proposed'"),
        ),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("criterion_id"),
        sa.ForeignKeyConstraint(
            ["directive_id"], ["change_directives.directive_id"]
        ),
        sa.CheckConstraint(
            "compilation_status IN ('proposed', 'approved', 'manually_authored')",
            name="ck_evidence_criteria_compilation_status",
        ),
        sa.CheckConstraint(
            "compilation_status = 'proposed' OR compiled_query IS NOT NULL",
            name="ck_evidence_criteria_query_present_when_finalized",
        ),
    )

    op.create_index(
        "ix_change_directive_evidence_criteria_directive_id",
        "change_directive_evidence_criteria",
        ["directive_id"],
    )

    op.execute(
        """
CREATE OR REPLACE FUNCTION change_directive_evidence_criteria_draft_only()
RETURNS TRIGGER AS $$
DECLARE
    parent_status TEXT;
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN NEW;
    END IF;
    SELECT status INTO parent_status
    FROM change_directives
    WHERE directive_id = NEW.directive_id;
    IF parent_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION
            'change_directive_evidence_criteria is mutable only when parent directive is in draft status (current: %)',
            parent_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_change_directive_evidence_criteria_draft_only
BEFORE UPDATE ON change_directive_evidence_criteria
FOR EACH ROW EXECUTE FUNCTION change_directive_evidence_criteria_draft_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON change_directive_evidence_criteria TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_change_directive_evidence_criteria_draft_only "
        "ON change_directive_evidence_criteria"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS change_directive_evidence_criteria_draft_only()"
    )
    op.drop_index(
        "ix_change_directive_evidence_criteria_directive_id",
        table_name="change_directive_evidence_criteria",
    )
    op.drop_table("change_directive_evidence_criteria")
