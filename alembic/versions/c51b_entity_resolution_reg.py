"""Chunk 51 — entity_resolution_registry table (D404).

Revision ID: c51b_entity_resolution_reg
Revises: c51a_federation_namespaces
Create Date: 2026-05-14

Canonical entity registry for cross-system entity resolution.
JSON-array embeddings in JSONB (no pgvector — D404).
Intentionally mutable: aliases and embeddings update as new data arrives.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c51b_entity_resolution_reg"
down_revision: str = "c51a_federation_namespaces"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "entity_resolution_registry",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("canonical_grace_id", sa.UUID(), nullable=False, unique=True),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("canonical_type", sa.Text(), nullable=False),
        sa.Column("aliases", JSONB, server_default=sa.text("'{}'::jsonb")),
        sa.Column("embedding_vector", JSONB, nullable=True),
        sa.Column("namespace_source", sa.Text(), nullable=True),
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
    )

    op.create_index(
        "ix_entity_resolution_registry_type_name",
        "entity_resolution_registry",
        ["canonical_type", "canonical_name"],
    )

    # D167 read-only role grant (conditional — role may not exist in dev).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
                EXECUTE 'GRANT SELECT ON entity_resolution_registry TO grace_readonly';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.drop_index("ix_entity_resolution_registry_type_name")
    op.drop_table("entity_resolution_registry")
