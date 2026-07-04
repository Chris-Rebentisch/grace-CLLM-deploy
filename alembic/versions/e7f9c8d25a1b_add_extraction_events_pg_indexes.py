"""add extraction_events_pg indexes for dashboard queries

Revision ID: e7f9c8d25a1b
Revises: c3d4e5f6a7b8
Create Date: 2026-04-21

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e7f9c8d25a1b"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_extraction_events_pg_completed_at",
        "extraction_events_pg",
        ["completed_at"],
    )
    op.create_index(
        "ix_extraction_events_pg_ontology_module",
        "extraction_events_pg",
        ["ontology_module"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_events_pg_ontology_module",
        table_name="extraction_events_pg",
    )
    op.drop_index(
        "ix_extraction_events_pg_completed_at",
        table_name="extraction_events_pg",
    )
