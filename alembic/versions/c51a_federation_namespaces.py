"""Chunk 51 — add federation columns to graph_namespaces (D402).

Revision ID: c51a_federation_namespaces
Revises: c50b_governance_decision_events
Create Date: 2026-05-14

Four additive columns for federation namespace metadata.
Existing rows receive default values; no data migration required.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "c51a_federation_namespaces"
down_revision: str = "c50b_governance_decision_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "graph_namespaces",
        sa.Column("namespace_type", sa.String(10), server_default="child"),
    )
    op.add_column(
        "graph_namespaces",
        sa.Column("label_prefix", sa.String(50), nullable=True),
    )
    op.add_column(
        "graph_namespaces",
        sa.Column("ontology_module", sa.String(100), nullable=True),
    )
    op.add_column(
        "graph_namespaces",
        sa.Column(
            "parent_namespace_id",
            sa.UUID(),
            sa.ForeignKey("graph_namespaces.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("graph_namespaces", "parent_namespace_id")
    op.drop_column("graph_namespaces", "ontology_module")
    op.drop_column("graph_namespaces", "label_prefix")
    op.drop_column("graph_namespaces", "namespace_type")
