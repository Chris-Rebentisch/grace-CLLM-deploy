"""create permission_matrices table (Chunk 42, D331)

Hash-chained append-only governance table for the Permission Matrix
artifact ratified by the operator.

Each row records:

* ``permission_matrix_id`` — surrogate UUID PK.
* ``payload_hash`` — SHA-256 of the canonical JSON serialization of
  the ``PermissionMatrix`` Pydantic payload.
* ``previous_hash`` — self-FK on ``payload_hash`` chaining to the prior
  matrix (NULL for the first matrix per organization).
* ``payload`` — full ``PermissionMatrix`` payload as JSONB.
* ``version_label`` — operator-supplied label (optional).
* ``created_by`` — operator handle.

Append-only via ``BEFORE UPDATE OR DELETE`` trigger raising
``check_violation``. The trigger honours an ``alembic.downgrading``
GUC escape valve so ``alembic downgrade`` round-trips cleanly
(Chunk 38 D291 / Chunk 41 D326 pattern).

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c42a_permission_matrices
Revises: c41b_runs_l5_l6
Create Date: 2026-05-08 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c42a_permission_matrices"
down_revision: Union[str, Sequence[str], None] = "c41b_runs_l5_l6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "permission_matrices",
        sa.Column(
            "permission_matrix_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("previous_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("created_by", sa.Text(), nullable=True),
        sa.Column("version_label", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("permission_matrix_id"),
        sa.UniqueConstraint(
            "payload_hash", name="uq_permission_matrices_payload_hash"
        ),
        sa.ForeignKeyConstraint(
            ["previous_hash"],
            ["permission_matrices.payload_hash"],
            name="fk_permission_matrices_previous_hash",
        ),
    )

    op.create_index(
        "ix_permission_matrices_created_at",
        "permission_matrices",
        [sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_permission_matrices_payload_hash",
        "permission_matrices",
        ["payload_hash"],
    )

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
    op.execute(
        """
CREATE TRIGGER trg_permission_matrices_append_only
BEFORE UPDATE OR DELETE ON permission_matrices
FOR EACH ROW EXECUTE FUNCTION permission_matrices_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON permission_matrices TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_permission_matrices_append_only "
        "ON permission_matrices"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS permission_matrices_append_only()"
    )
    op.drop_index(
        "ix_permission_matrices_payload_hash",
        table_name="permission_matrices",
    )
    op.drop_index(
        "ix_permission_matrices_created_at",
        table_name="permission_matrices",
    )
    op.drop_table("permission_matrices")
