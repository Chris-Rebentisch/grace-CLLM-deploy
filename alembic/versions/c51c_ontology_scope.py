"""Chunk 51 — ontology_scope column on ontology_versions (D405).

Revision ID: c51c_ontology_scope
Revises: c51b_entity_resolution_reg
Create Date: 2026-05-14

Additive column for two-tier federation scope enforcement.
Existing rows default to 'single' (pre-federation behavior).
"""

from alembic import op
import sqlalchemy as sa

revision: str = "c51c_ontology_scope"
down_revision: str = "c51b_entity_resolution_reg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ontology_versions",
        sa.Column("ontology_scope", sa.String(10), server_default="single"),
    )


def downgrade() -> None:
    op.drop_column("ontology_versions", "ontology_scope")
