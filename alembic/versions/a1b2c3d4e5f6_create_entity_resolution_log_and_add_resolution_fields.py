"""create entity_resolution_log and add resolution fields to extraction_claims

Revision ID: a1b2c3d4e5f6
Revises: f9a2c3d4e5b6
Create Date: 2026-04-09

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "f9a2c3d4e5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A. Create entity_resolution_log table
    op.create_table(
        "entity_resolution_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("extracted_name", sa.String(500), nullable=False),
        sa.Column("extracted_type", sa.String(100), nullable=False),
        sa.Column("matched_grace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("matched_name", sa.String(500), nullable=True),
        sa.Column("resolution_tier", sa.String(20), nullable=False),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("blocking_key", sa.String(200), nullable=False),
        sa.Column("candidate_count", sa.Integer(), nullable=True),
        sa.Column("candidates_json", postgresql.JSONB(), nullable=True),
        sa.Column("resolution_note", sa.String(200), nullable=True),
        sa.Column("extraction_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("batch_id", sa.String(64), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_index(
        "ix_resolution_log_extraction_event_id",
        "entity_resolution_log",
        ["extraction_event_id"],
    )
    op.create_index(
        "ix_resolution_log_batch_id",
        "entity_resolution_log",
        ["batch_id"],
    )
    op.create_index(
        "ix_resolution_log_tier",
        "entity_resolution_log",
        ["resolution_tier"],
    )
    op.create_index(
        "ix_resolution_log_type_resolved",
        "entity_resolution_log",
        ["extracted_type", "resolved_at"],
    )

    # B. Add columns to extraction_claims table
    op.add_column(
        "extraction_claims",
        sa.Column("resolved_entity_grace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "extraction_claims",
        sa.Column("resolved_subject_grace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "extraction_claims",
        sa.Column("resolved_object_grace_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "extraction_claims",
        sa.Column("resolution_note", sa.String(200), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_claims", "resolution_note")
    op.drop_column("extraction_claims", "resolved_object_grace_id")
    op.drop_column("extraction_claims", "resolved_subject_grace_id")
    op.drop_column("extraction_claims", "resolved_entity_grace_id")

    op.drop_index("ix_resolution_log_type_resolved", table_name="entity_resolution_log")
    op.drop_index("ix_resolution_log_tier", table_name="entity_resolution_log")
    op.drop_index("ix_resolution_log_batch_id", table_name="entity_resolution_log")
    op.drop_index("ix_resolution_log_extraction_event_id", table_name="entity_resolution_log")
    op.drop_table("entity_resolution_log")
