"""create recon_divergence_maps table

Chunk 37, D284 + B4 resolution. Persists Cross-Executive Divergence
Maps (the OM4OV ``compute_entity_level_diff`` reuse output) keyed by
``(segment_id, reviewer_a, reviewer_b)``. Append-only; the GET-latest
route ranks by ``generated_at DESC``.

Creates:
  - recon_divergence_maps table — additive; no destructive ALTER.
  - ix_recon_divergence_maps_latest compound index for the GET-latest
    query pattern (segment_id, reviewer_a, reviewer_b, generated_at DESC).
  - GRANT SELECT to grace_readonly (D167).

Revision ID: c37c_recon_divergence_maps
Revises: c37b_apscheduler_jobs
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c37c_recon_divergence_maps"
down_revision: Union[str, Sequence[str], None] = "c37b_apscheduler_jobs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recon_divergence_maps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("segment_id", sa.Text(), nullable=True),
        sa.Column("reviewer_a", sa.Text(), nullable=False),
        sa.Column("reviewer_b", sa.Text(), nullable=False),
        sa.Column(
            "version_a_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "version_b_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "buckets",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Compound index supporting GET .../latest?segment_id=&reviewer_a=&reviewer_b=
    op.create_index(
        "ix_recon_divergence_maps_latest",
        "recon_divergence_maps",
        ["segment_id", "reviewer_a", "reviewer_b", sa.text("generated_at DESC")],
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON recon_divergence_maps TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recon_divergence_maps_latest",
        table_name="recon_divergence_maps",
    )
    op.drop_table("recon_divergence_maps")
