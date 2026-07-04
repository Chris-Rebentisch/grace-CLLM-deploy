"""create sensitivity_classification_reports + denorm columns on permission_matrices (Chunk 43, D344)

Immutable governance table backing the Sensitivity Classification Report
generated over the active ``PermissionMatrix``. Pattern mirrors the
``permission_matrices`` table (c42a):

* BEFORE UPDATE OR DELETE trigger raises ``check_violation`` outside of
  ``alembic.downgrading`` so the table is append-only at runtime but
  ``alembic downgrade`` round-trips cleanly (Chunk 38 D291 / Chunk 41 D326).
* ``GRANT SELECT ... TO grace_readonly`` (D167).

Also adds three nullable denormalized columns to ``permission_matrices``
so the Permissions UI can read coverage-band / tag-count / untagged-rule
count without joining the report. NULL on pre-existing rows is the
documented contract.

Revision ID: c43a_sensitivity_reports
Revises: c42d_hypothesis_one_running
Create Date: 2026-05-09 18:00:00.000000

Note: the spec-named revision id ``c43a_sensitivity_classification_reports``
(38 chars) overflowed ``alembic_version.version_num VARCHAR(32)``.
Shortened to ``c43a_sensitivity_reports`` (24 chars) per the same
remediation pattern used on c42d during chunk-43 code-stage resume.

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c43a_sensitivity_reports"
down_revision: Union[str, Sequence[str], None] = "c42d_hypothesis_one_running"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sensitivity_classification_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "permission_matrix_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "tag_inventory",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "coverage_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "untagged_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tag_hygiene_findings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("coverage_band", sa.Text(), nullable=True),
        sa.Column("coverage_score", sa.REAL(), nullable=True),
        sa.Column("corpus_below_floor", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["permission_matrix_id"],
            ["permission_matrices.permission_matrix_id"],
            name="fk_sensitivity_reports_matrix_id",
        ),
        sa.CheckConstraint(
            "coverage_band IS NULL OR coverage_band IN ('high','medium','low')",
            name="ck_sensitivity_reports_coverage_band",
        ),
    )

    op.create_index(
        "ix_sensitivity_reports_matrix_id_generated_at",
        "sensitivity_classification_reports",
        ["permission_matrix_id", sa.text("generated_at DESC")],
    )

    op.execute(
        """
CREATE OR REPLACE FUNCTION sensitivity_classification_reports_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'sensitivity_classification_reports is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_sensitivity_classification_reports_append_only
BEFORE UPDATE OR DELETE ON sensitivity_classification_reports
FOR EACH ROW EXECUTE FUNCTION sensitivity_classification_reports_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON sensitivity_classification_reports TO grace_readonly';
    END IF;
END
$$;
"""
    )

    # Denormalized columns on permission_matrices. NULL on pre-existing rows
    # is the documented contract (Chunk 43 spec §6 CP1 step 2).
    op.add_column(
        "permission_matrices",
        sa.Column("coverage_band", sa.Text(), nullable=True),
    )
    op.add_column(
        "permission_matrices",
        sa.Column("tag_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "permission_matrices",
        sa.Column("untagged_rule_count", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_permission_matrices_coverage_band",
        "permission_matrices",
        "coverage_band IS NULL OR coverage_band IN ('high','medium','low')",
    )

    # The c42a append-only trigger blocks ALL UPDATEs on permission_matrices.
    # c43a adds three nullable denormalized columns (coverage_band, tag_count,
    # untagged_rule_count) that the report writer must UPDATE after each
    # report is persisted. Redefine the trigger function so DELETE is still
    # blocked outright, and UPDATEs are permitted ONLY when every non-denorm
    # column is unchanged. Hash-chain immutability of permission_matrix_id /
    # payload / payload_hash / previous_hash / created_at / created_by /
    # version_label is preserved (R5 / D331).
    op.execute(
        """
CREATE OR REPLACE FUNCTION permission_matrices_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'permission_matrices is append-only'
            USING ERRCODE = 'check_violation';
    END IF;
    -- UPDATE: permit only changes confined to the three denormalized
    -- columns added in c43a.
    IF NEW.permission_matrix_id IS DISTINCT FROM OLD.permission_matrix_id
        OR NEW.payload IS DISTINCT FROM OLD.payload
        OR NEW.payload_hash IS DISTINCT FROM OLD.payload_hash
        OR NEW.previous_hash IS DISTINCT FROM OLD.previous_hash
        OR NEW.created_at IS DISTINCT FROM OLD.created_at
        OR NEW.created_by IS DISTINCT FROM OLD.created_by
        OR NEW.version_label IS DISTINCT FROM OLD.version_label
    THEN
        RAISE EXCEPTION
            'permission_matrices is append-only'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")

    # Restore the c42a append-only function to its original (block-all)
    # behavior before dropping the denormalized columns it was relaxed for.
    op.execute(
        """
CREATE OR REPLACE FUNCTION permission_matrices_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'permission_matrices is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )

    op.drop_constraint(
        "ck_permission_matrices_coverage_band",
        "permission_matrices",
        type_="check",
    )
    op.drop_column("permission_matrices", "untagged_rule_count")
    op.drop_column("permission_matrices", "tag_count")
    op.drop_column("permission_matrices", "coverage_band")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_sensitivity_classification_reports_append_only "
        "ON sensitivity_classification_reports"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS sensitivity_classification_reports_append_only()"
    )
    op.drop_index(
        "ix_sensitivity_reports_matrix_id_generated_at",
        table_name="sensitivity_classification_reports",
    )
    op.drop_table("sensitivity_classification_reports")
