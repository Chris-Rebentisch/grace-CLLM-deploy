"""Chunk 50 — cooling-period state columns on schema_proposals (D399).

Revision ID: c50a_cooling_columns
Revises: c49a_calibration_decisions
Create Date: 2026-05-14

Adds four nullable columns for cooling-period state tracking and widens the
existing Chunk 47 append-only trigger UPDATE allowlist to permit their mutation.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "c50a_cooling_columns"
down_revision: str = "c49a_calibration_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Four additive columns per spec §3.2 (D399).
    op.add_column(
        "schema_proposals",
        sa.Column("cooling_outcome", sa.String(20), nullable=True),
    )
    op.add_column(
        "schema_proposals",
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "schema_proposals",
        sa.Column("reverted_by", sa.Text(), nullable=True),
    )
    op.add_column(
        "schema_proposals",
        sa.Column(
            "reverted_proposal_id",
            sa.UUID(),
            sa.ForeignKey("schema_proposals.id"),
            nullable=True,
        ),
    )

    # Widen the Chunk 47 append-only trigger to permit UPDATE of the four
    # new cooling columns.  The immutable-column check list is unchanged —
    # only the "permitted by omission" set grows.
    #
    # Invariant: Chunk 47 append-only trigger on schema_proposals.
    # Carve-out: UPDATE of cooling_outcome, reverted_at, reverted_by,
    #            reverted_proposal_id permitted.
    # Authorization: D399.
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
                -- Mutable columns permitted by design:
                --   status, human_decision, reviewer, modification_distance,
                --   modified_diff, resulting_version_id, proposed_diff (legacy),
                --   applied_autonomously, trust_score_at_time,
                --   cooling_period_expires_at, cooling_period_reverted,
                --   metadata_extra, overflow,
                --   cooling_outcome, reverted_at, reverted_by,
                --   reverted_proposal_id  (D399, Chunk 50)
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Restore original Chunk 47 trigger (without the four new columns
    # in the permitted-update set — they are dropped below anyway).
    op.execute("""
        CREATE OR REPLACE FUNCTION schema_proposals_append_only()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'schema_proposals is append-only: DELETE is forbidden'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' THEN
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

    op.drop_column("schema_proposals", "reverted_proposal_id")
    op.drop_column("schema_proposals", "reverted_by")
    op.drop_column("schema_proposals", "reverted_at")
    op.drop_column("schema_proposals", "cooling_outcome")
