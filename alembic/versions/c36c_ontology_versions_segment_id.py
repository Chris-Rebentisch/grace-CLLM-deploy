"""add segment_id to ontology_versions; recreate active-version partial unique index

Chunk 36, D278. Adds segment_id TEXT NULL (loose-string; FK to a future
segments table deferred to Chunk 40). Recreates the partial unique index
that previously enforced single-active-version-globally as a composite
partial unique index keyed by (segment_id, reviewer, is_active=TRUE).

Legacy rows (segment_id IS NULL) continue to participate in the legacy
single-active-version-per-base invariant; new rows partitioned by
segment-and-reviewer can coexist as multiple "current" versions.

Revision ID: c36c_ontology_versions_segment_id
Revises: c36b_review_sessions_recon_columns
Create Date: 2026-05-06 00:00:02.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c36c_ontology_segment_id"
down_revision: Union[str, Sequence[str], None] = "c36b_review_sessions_recon"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ontology_versions",
        sa.Column("segment_id", sa.Text(), nullable=True),
    )
    # Recreate the partial unique index as a composite (segment_id, reviewer,
    # is_active) WHERE is_active = TRUE.
    op.drop_index(
        "ix_ontology_versions_is_active",
        table_name="ontology_versions",
    )
    op.create_index(
        "ix_ontology_versions_active_segment",
        "ontology_versions",
        ["segment_id", "reviewer", "is_active"],
        unique=True,
        postgresql_where=sa.text("is_active = TRUE"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ontology_versions_active_segment",
        table_name="ontology_versions",
    )
    op.create_index(
        "ix_ontology_versions_is_active",
        "ontology_versions",
        ["is_active"],
        postgresql_where=sa.text("is_active = TRUE"),
    )
    op.drop_column("ontology_versions", "segment_id")
