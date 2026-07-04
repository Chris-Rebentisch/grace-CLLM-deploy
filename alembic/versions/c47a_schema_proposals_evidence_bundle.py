"""Chunk 47 — additive columns + append-only trigger for schema_proposals (D387/D388).

Revision ID: c47a
Revises: c46a_d364_agent_identity
Create Date: 2026-05-13
"""

from alembic import op
import sqlalchemy as sa

revision: str = "c47a"
down_revision: str = "c46a_d364_agent_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Four additive columns per spec §3.2.
    op.add_column(
        "schema_proposals",
        sa.Column("ontology_module", sa.Text(), nullable=True),
    )
    op.add_column(
        "schema_proposals",
        sa.Column("dedup_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "schema_proposals",
        sa.Column("overflow", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "schema_proposals",
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Dedup hash index for Phase 1 / Phase 2 lookups.
    op.create_index(
        "ix_schema_proposals_dedup_hash",
        "schema_proposals",
        ["dedup_hash"],
    )

    # Append-only trigger: DELETE is blocked; UPDATE is restricted to
    # mutable columns. `priority` is in the immutable column list —
    # set at generation time by classify_tier(), immutable post-INSERT.
    # Invariant: D387 append-only trigger (Chunk 47).
    # Carve-out: UPDATE permitted on mutable columns only.
    # Authorization: chunk-47-spec-v2-FINAL §3.2.
    op.execute("""
        CREATE OR REPLACE FUNCTION schema_proposals_append_only()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'schema_proposals is append-only: DELETE is forbidden'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                -- Immutable columns: raise on any change.
                IF OLD.id IS DISTINCT FROM NEW.id
                   OR OLD.created_at IS DISTINCT FROM NEW.created_at
                   OR OLD.proposal_type IS DISTINCT FROM NEW.proposal_type
                   OR OLD.change_tier IS DISTINCT FROM NEW.change_tier
                   OR OLD.priority IS DISTINCT FROM NEW.priority
                   OR OLD.kgcl_command IS DISTINCT FROM NEW.kgcl_command
                   OR OLD.proposed_diff IS DISTINCT FROM NEW.proposed_diff
                   OR OLD.evidence IS DISTINCT FROM NEW.evidence
                   OR OLD.raw_confidence IS DISTINCT FROM NEW.raw_confidence
                   OR OLD.current_schema_version_id IS DISTINCT FROM NEW.current_schema_version_id
                   OR OLD.signal_type IS DISTINCT FROM NEW.signal_type
                   OR OLD.ontology_module IS DISTINCT FROM NEW.ontology_module
                   OR OLD.dedup_hash IS DISTINCT FROM NEW.dedup_hash
                   OR OLD.generated_at IS DISTINCT FROM NEW.generated_at
                THEN
                    RAISE EXCEPTION 'schema_proposals: immutable columns cannot be updated'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        CREATE TRIGGER trg_schema_proposals_append_only
        BEFORE UPDATE OR DELETE ON schema_proposals
        FOR EACH ROW EXECUTE FUNCTION schema_proposals_append_only();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_schema_proposals_append_only ON schema_proposals")
    op.execute("DROP FUNCTION IF EXISTS schema_proposals_append_only()")
    op.drop_index("ix_schema_proposals_dedup_hash", table_name="schema_proposals")
    op.drop_column("schema_proposals", "generated_at")
    op.drop_column("schema_proposals", "overflow")
    op.drop_column("schema_proposals", "dedup_hash")
    op.drop_column("schema_proposals", "ontology_module")
