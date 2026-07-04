"""Chunk 65 — schema_proposals bootstrap-correction carve-out (D448).

Adds ``is_correction BOOLEAN NOT NULL DEFAULT false`` to ``schema_proposals``
and extends the existing ``schema_proposals_append_only()`` trigger with a
narrow time-bounded correction carve-out for ``proposal_type`` only.

Invariant: ``schema_proposals`` append-only trigger (Chunk 47).
Carve-out: narrow ``proposal_type``-only correction within 60-minute window,
           one-shot ``is_correction`` flip (false → true).
Authorization: D448.

Revision ID: c65b_schema_proposals_correction
Revises: c65a_kill_switch_history
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c65b_schema_proposals_correction"
down_revision: Union[str, Sequence[str], None] = "c65a_kill_switch_history"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "schema_proposals",
        sa.Column("is_correction", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # Extend the Chunk 50 (c50a) append-only trigger with a D448 correction
    # carve-out. The carve-out permits proposal_type UPDATE within 60 minutes
    # of row creation when is_correction flips false → true, one-shot only.
    #
    # Invariant: schema_proposals append-only trigger (Chunk 47, widened Chunk 50).
    # Carve-out: narrow proposal_type-only correction within 60-minute window,
    #            one-shot is_correction flip (false → true).
    # Authorization: D448.
    op.execute("""
        CREATE OR REPLACE FUNCTION schema_proposals_append_only()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'schema_proposals is append-only: DELETE is forbidden'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                -- D448 correction carve-out: permit proposal_type UPDATE within
                -- 60 minutes of row creation when is_correction flips false → true.
                -- One-shot only: if is_correction is already true, reject.
                IF OLD.proposal_type IS DISTINCT FROM NEW.proposal_type THEN
                    IF OLD.is_correction = true THEN
                        RAISE EXCEPTION 'schema_proposals: correction already applied (one-shot)'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF NEW.is_correction IS DISTINCT FROM true THEN
                        RAISE EXCEPTION 'schema_proposals: proposal_type update requires is_correction=true'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    IF (now() - OLD.created_at) > interval '60 minutes' THEN
                        RAISE EXCEPTION 'schema_proposals: correction window expired (60 minutes)'
                            USING ERRCODE = 'check_violation';
                    END IF;
                    -- Correction permitted — skip the normal immutable-column check
                    -- for proposal_type. All other immutable columns still checked below.
                    IF OLD.id IS DISTINCT FROM NEW.id
                       OR OLD.created_at IS DISTINCT FROM NEW.created_at
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
                    RETURN NEW;
                END IF;

                -- Normal immutable-column check (includes proposal_type).
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
                --   is_correction  (D448, Chunk 65 — mutable for the correction carve-out)
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    # Restore c50a trigger body verbatim (without correction carve-out).
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

    op.drop_column("schema_proposals", "is_correction")
