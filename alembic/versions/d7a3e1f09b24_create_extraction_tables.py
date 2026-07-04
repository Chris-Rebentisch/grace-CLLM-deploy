"""create extraction_claims and extraction_events_pg tables

Revision ID: d7a3e1f09b24
Revises: c4e8f2a17b93
Create Date: 2026-04-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d7a3e1f09b24"
down_revision: Union[str, None] = "c4e8f2a17b93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- extraction_claims ---
    op.create_table(
        "extraction_claims",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("claim_fingerprint", sa.String(64), nullable=True),
        sa.Column("extraction_unit_id", sa.String(64), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=True),
        sa.Column("relationship_type", sa.String(100), nullable=True),
        sa.Column("subject_name", sa.String(500), nullable=False),
        sa.Column("predicate", sa.String(200), nullable=False),
        sa.Column("object_name", sa.String(500), nullable=True),
        sa.Column("properties_json", postgresql.JSONB, nullable=True),
        sa.Column("evidence_spans", postgresql.JSONB, nullable=True),
        sa.Column("verdict", sa.String(20), nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="auto_accepted"
        ),
        sa.Column(
            "decision_source", sa.String(20), nullable=False, server_default="pipeline"
        ),
        sa.Column("constraint_violations", postgresql.JSONB, nullable=True),
        sa.Column("supersedes_claim_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_document_id", sa.String(200), nullable=False),
        sa.Column("source_chunk_id", sa.String(200), nullable=False),
        sa.Column("ontology_module", sa.String(100), nullable=True),
        sa.Column("schema_version", sa.Integer, nullable=True),
        sa.Column("prompt_template_id", sa.String(100), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column("model_temperature", sa.Float, nullable=True),
        sa.Column("model_max_tokens", sa.Integer, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_extraction_claims_claim_id", "extraction_claims", ["claim_id"], unique=True)
    op.create_index("ix_extraction_claims_extraction_unit_id", "extraction_claims", ["extraction_unit_id"])
    op.create_index("ix_extraction_claims_source_document_id", "extraction_claims", ["source_document_id"])
    op.create_index("ix_extraction_claims_status", "extraction_claims", ["status"])
    op.create_index("ix_extraction_claims_verdict", "extraction_claims", ["verdict"])

    # --- extraction_events_pg ---
    op.create_table(
        "extraction_events_pg",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", sa.String(200), nullable=False),
        sa.Column("ontology_module", sa.String(100), nullable=True),
        sa.Column("schema_version", sa.Integer, nullable=True),
        sa.Column("provider_used", sa.String(50), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("chunks_total", sa.Integer, nullable=True),
        sa.Column("chunks_succeeded", sa.Integer, nullable=True),
        sa.Column("chunks_failed", sa.Integer, nullable=True),
        sa.Column("entities_extracted", sa.Integer, nullable=True),
        sa.Column("relationships_extracted", sa.Integer, nullable=True),
        sa.Column("claims_accepted", sa.Integer, nullable=True),
        sa.Column("claims_quarantined", sa.Integer, nullable=True),
        sa.Column("avg_confidence", sa.Float, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="running"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index("ix_extraction_events_pg_event_id", "extraction_events_pg", ["event_id"], unique=True)
    op.create_index("ix_extraction_events_pg_batch_id", "extraction_events_pg", ["batch_id"])
    op.create_index("ix_extraction_events_pg_source_document_id", "extraction_events_pg", ["source_document_id"])


def downgrade() -> None:
    op.drop_index("ix_extraction_events_pg_source_document_id", table_name="extraction_events_pg")
    op.drop_index("ix_extraction_events_pg_batch_id", table_name="extraction_events_pg")
    op.drop_index("ix_extraction_events_pg_event_id", table_name="extraction_events_pg")
    op.drop_table("extraction_events_pg")

    op.drop_index("ix_extraction_claims_verdict", table_name="extraction_claims")
    op.drop_index("ix_extraction_claims_status", table_name="extraction_claims")
    op.drop_index("ix_extraction_claims_source_document_id", table_name="extraction_claims")
    op.drop_index("ix_extraction_claims_extraction_unit_id", table_name="extraction_claims")
    op.drop_index("ix_extraction_claims_claim_id", table_name="extraction_claims")
    op.drop_table("extraction_claims")
