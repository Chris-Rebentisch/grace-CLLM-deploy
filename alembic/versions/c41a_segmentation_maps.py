"""create segmentation_maps table (Chunk 41, D326)

Hash-chained append-only governance table for the Layer 7
Segmentation Map artifact. Each row records:

* ``segmentation_map_id`` — surrogate UUID PK.
* ``decomposition_run_id`` — FK to the ``decomposition_runs`` row
  whose Layer 4 hypotheses + Layer 5 decision + Layer 6 validation
  produced the map.
* ``payload_hash`` — SHA-256 of the canonical JSON serialization
  of the ``SegmentationMap`` Pydantic payload.
* ``previous_hash`` — self-FK on ``payload_hash`` chaining to the
  prior map for the same ``decomposition_run_id`` (NULL for the
  first map per run).
* ``payload`` — full ``SegmentationMap`` payload as JSONB.
* ``null_hypothesis_accepted`` — denormalized for query efficiency.

Append-only via ``BEFORE UPDATE OR DELETE`` trigger raising
``check_violation``. The trigger honours an ``alembic.downgrading``
GUC escape valve so ``alembic downgrade`` round-trips cleanly
(Chunk 38 D291 pattern).

``GRANT SELECT`` to ``grace_readonly`` (D167).

Revision ID: c41a_segmentation_maps
Revises: c40a_decomposition_runs
Create Date: 2026-05-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c41a_segmentation_maps"
down_revision: Union[str, Sequence[str], None] = "c40a_decomposition_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "segmentation_maps",
        sa.Column(
            "segmentation_map_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "decomposition_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("schema_version", sa.Text(), nullable=False),
        sa.Column("payload_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("previous_hash", sa.CHAR(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("null_hypothesis_accepted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("segmentation_map_id"),
        sa.ForeignKeyConstraint(
            ["decomposition_run_id"], ["decomposition_runs.run_id"]
        ),
        sa.UniqueConstraint("payload_hash", name="uq_segmentation_maps_payload_hash"),
        sa.ForeignKeyConstraint(
            ["previous_hash"],
            ["segmentation_maps.payload_hash"],
            name="fk_segmentation_maps_previous_hash",
        ),
    )

    op.create_index(
        "ix_segmentation_maps_run_created",
        "segmentation_maps",
        ["decomposition_run_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_segmentation_maps_payload_hash",
        "segmentation_maps",
        ["payload_hash"],
    )

    op.execute(
        """
CREATE OR REPLACE FUNCTION segmentation_maps_append_only()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'segmentation_maps is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_segmentation_maps_append_only
BEFORE UPDATE OR DELETE ON segmentation_maps
FOR EACH ROW EXECUTE FUNCTION segmentation_maps_append_only();
"""
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON segmentation_maps TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("SET LOCAL alembic.downgrading = 'true'")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_segmentation_maps_append_only "
        "ON segmentation_maps"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS segmentation_maps_append_only()"
    )
    op.drop_index(
        "ix_segmentation_maps_payload_hash",
        table_name="segmentation_maps",
    )
    op.drop_index(
        "ix_segmentation_maps_run_created",
        table_name="segmentation_maps",
    )
    op.drop_table("segmentation_maps")
