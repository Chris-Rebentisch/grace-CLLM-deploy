"""create mine_samples table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-10

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mine_samples",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_text_hash", sa.Text(), nullable=False),
        sa.Column("source_facts", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("judgments", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("total_facts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("recovered_facts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("retention_score", sa.Float(), nullable=False, server_default=sa.text("0.0")),
        sa.Column("extraction_model", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("judge_model", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("schema_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sampled_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("metadata_extra", postgresql.JSONB(),
                  server_default=sa.text("'{}'::jsonb")),
    )

    # Indexes
    op.create_index("idx_mine_samples_document_id", "mine_samples", ["document_id"])
    op.create_index("idx_mine_samples_sampled_at", "mine_samples", ["sampled_at"])

    # Dedup unique index: (source_text_hash, extraction_model, judge_model)
    # Cache invalidation on ontology change is manual — delete rows to
    # force re-run after major ontology updates.
    op.create_index(
        "uq_mine_samples_dedup",
        "mine_samples",
        ["source_text_hash", "extraction_model", "judge_model"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_mine_samples_dedup", table_name="mine_samples")
    op.drop_index("idx_mine_samples_sampled_at", table_name="mine_samples")
    op.drop_index("idx_mine_samples_document_id", table_name="mine_samples")
    op.drop_table("mine_samples")
