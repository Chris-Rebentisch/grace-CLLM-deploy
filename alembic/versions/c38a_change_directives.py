"""create change_directives table

Chunk 38, D294. Main entity table for the Change_Directives foundation.
Two tiers ``Operational_Adjustment`` and ``Strategic_Initiative`` are
discriminated by the ``tier`` column with a CHECK constraint; ``status``
is bounded to the five-state lifecycle (D292).

The row itself is intentionally mutable — draft-stage body PATCHes
edit the allowlisted columns; ``transition()`` writes ``status`` +
``status_updated_at`` post-INSERT. Append-only audit lives in the
sibling ``change_directive_state_transitions`` table (c38b).

Creates:
  - change_directives table.
  - ix_change_directives_authored_by, ix_change_directives_status,
    ix_change_directives_segments_gin indexes.
  - GRANT SELECT to grace_readonly (D167).

Revision ID: c38a_change_directives
Revises: c37d_recon_dr_reports
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c38a_change_directives"
down_revision: Union[str, Sequence[str], None] = "c37d_recon_dr_reports"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "change_directives",
        sa.Column(
            "directive_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "authored_by", postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column(
            "authored_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column(
            "status_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "visibility",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'permission_matrix_default'"),
        ),
        sa.Column(
            "visibility_named_list",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("visibility_role_cluster", sa.Text(), nullable=True),
        sa.Column(
            "affected_segments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "extension_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "superseded_by_directive_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("target_state_description", sa.Text(), nullable=True),
        sa.Column("realization_horizon", sa.Text(), nullable=True),
        sa.Column("responsible_executive", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("directive_id"),
        sa.CheckConstraint(
            "tier IN ('Operational_Adjustment', 'Strategic_Initiative')",
            name="ck_change_directives_tier",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'realized', 'abandoned', 'superseded')",
            name="ck_change_directives_status",
        ),
        sa.CheckConstraint(
            "visibility IN ('permission_matrix_default', 'private_to_self', 'private_to_named_list', 'scoped_to_role_cluster')",
            name="ck_change_directives_visibility",
        ),
    )

    op.create_index(
        "ix_change_directives_authored_by",
        "change_directives",
        ["authored_by"],
    )
    op.create_index(
        "ix_change_directives_status",
        "change_directives",
        ["status"],
    )
    op.create_index(
        "ix_change_directives_segments_gin",
        "change_directives",
        ["affected_segments"],
        postgresql_using="gin",
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON change_directives TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_change_directives_segments_gin", table_name="change_directives"
    )
    op.drop_index(
        "ix_change_directives_status", table_name="change_directives"
    )
    op.drop_index(
        "ix_change_directives_authored_by", table_name="change_directives"
    )
    op.drop_table("change_directives")
