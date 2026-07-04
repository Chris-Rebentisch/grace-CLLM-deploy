"""Chunk 49 — calibration_decisions (append-only + update-forbidden) + trust_scores (mutable).

Revision ID: c49a_calibration_decisions
Revises: c47a
Create Date: 2026-05-14

Invariant: calibration_decisions is write-once — immutable after INSERT.
Carve-out: none — both DELETE and UPDATE triggers raise check_violation.
Authorization source: chunk-49-spec-v6-FINAL.md §3.2 (D394).
"""

from alembic import op
import sqlalchemy as sa

revision: str = "c49a_calibration_decisions"
down_revision: str = "c47a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # calibration_decisions: append-only raw decision log.
    op.execute("""
        CREATE TABLE calibration_decisions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            proposal_id UUID NOT NULL REFERENCES schema_proposals(id),
            change_tier INTEGER NOT NULL CHECK (change_tier BETWEEN 1 AND 3),
            raw_confidence FLOAT NOT NULL CHECK (raw_confidence BETWEEN 0.0 AND 1.0),
            decision VARCHAR(20) NOT NULL CHECK (decision IN ('approved', 'rejected')),
            modification_distance FLOAT CHECK (modification_distance BETWEEN 0.0 AND 1.0),
            ontology_module TEXT,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)

    op.execute("""
        CREATE INDEX ix_calibration_decisions_tier_recorded
            ON calibration_decisions (change_tier, recorded_at DESC)
    """)

    # Immutability trigger: DELETE forbidden.
    op.execute("""
        CREATE OR REPLACE FUNCTION calibration_decisions_no_delete()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'DELETE on calibration_decisions is forbidden'
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER trg_calibration_decisions_no_delete
            BEFORE DELETE ON calibration_decisions
            FOR EACH ROW EXECUTE FUNCTION calibration_decisions_no_delete()
    """)

    # Immutability trigger: UPDATE forbidden.
    op.execute("""
        CREATE OR REPLACE FUNCTION calibration_decisions_no_update()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'UPDATE on calibration_decisions is forbidden'
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER trg_calibration_decisions_no_update
            BEFORE UPDATE ON calibration_decisions
            FOR EACH ROW EXECUTE FUNCTION calibration_decisions_no_update()
    """)

    # trust_scores: mutable, one row per tier.
    op.execute("""
        CREATE TABLE trust_scores (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tier INTEGER NOT NULL UNIQUE CHECK (tier BETWEEN 1 AND 3),
            trust_score FLOAT NOT NULL DEFAULT 0.0,
            autonomy_threshold FLOAT NOT NULL DEFAULT 0.95,
            autonomy_enabled BOOLEAN NOT NULL DEFAULT false,
            window_size INTEGER NOT NULL DEFAULT 50,
            min_reviews_for_calibration INTEGER NOT NULL DEFAULT 50,
            risk_tolerance FLOAT NOT NULL DEFAULT 0.95,
            total_decisions INTEGER NOT NULL DEFAULT 0,
            regression_detected BOOLEAN NOT NULL DEFAULT false,
            last_computed_at TIMESTAMPTZ
        )
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_calibration_decisions_no_delete ON calibration_decisions")
    op.execute("DROP TRIGGER IF EXISTS trg_calibration_decisions_no_update ON calibration_decisions")
    op.execute("DROP FUNCTION IF EXISTS calibration_decisions_no_delete()")
    op.execute("DROP FUNCTION IF EXISTS calibration_decisions_no_update()")
    op.execute("DROP TABLE IF EXISTS trust_scores")
    op.execute("DROP TABLE IF EXISTS calibration_decisions")
