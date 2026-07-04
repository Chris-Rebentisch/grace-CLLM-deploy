"""create cq_candidates table

Chunk 29, D227. CQ candidate pre-population with Crawl4AI. Validation
status defaults to 'quarantined' per Phase5-Enhancement-Specs §3.6
hard contract. FK to elicitation_sessions(session_id).

Revision ID: c29b_cq_candidates
Revises: c29a_elicitation_sessions
Create Date: 2026-05-01 10:01:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c29b_cq_candidates"
down_revision: Union[str, Sequence[str], None] = "c29a_elicitation_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types
    cq_candidate_source_origin = postgresql.ENUM(
        "local_documents", "web_presence", "ontology_seed",
        name="cq_candidate_source_origin",
        create_type=True,
    )
    cq_candidate_validation_status = postgresql.ENUM(
        "quarantined", "approved", "rejected", "human_authored",
        name="cq_candidate_validation_status",
        create_type=True,
    )

    op.create_table(
        "cq_candidates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("elicitation_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("cq_text", sa.Text(), nullable=False),
        sa.Column("cq_type", sa.Text(), nullable=False),
        sa.Column(
            "source_origin",
            cq_candidate_source_origin,
            nullable=False,
        ),
        sa.Column(
            "validation_status",
            cq_candidate_validation_status,
            nullable=False,
            server_default="quarantined",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_cq_candidates_session_id", "cq_candidates", ["session_id"]
    )
    op.create_index(
        "ix_cq_candidates_source_origin", "cq_candidates", ["source_origin"]
    )
    op.create_index(
        "ix_cq_candidates_validation_status",
        "cq_candidates",
        ["validation_status"],
    )
    op.create_index(
        "ix_cq_candidates_created_at", "cq_candidates", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_cq_candidates_created_at", table_name="cq_candidates")
    op.drop_index(
        "ix_cq_candidates_validation_status", table_name="cq_candidates"
    )
    op.drop_index(
        "ix_cq_candidates_source_origin", table_name="cq_candidates"
    )
    op.drop_index("ix_cq_candidates_session_id", table_name="cq_candidates")
    op.drop_table("cq_candidates")
    op.execute("DROP TYPE IF EXISTS cq_candidate_validation_status")
    op.execute("DROP TYPE IF EXISTS cq_candidate_source_origin")
