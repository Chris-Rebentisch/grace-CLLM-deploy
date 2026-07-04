"""Chunk 50 — governance_decision_events table (strict append-only).

Revision ID: c50b_governance_decision_events
Revises: c50a_cooling_columns
Create Date: 2026-05-14

Write-once table recording all autonomous agent and operator governance
decisions.  Both DELETE and UPDATE raise check_violation.
"""

from alembic import op
import sqlalchemy as sa

revision: str = "c50b_governance_decision_events"
down_revision: str = "c50a_cooling_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "governance_decision_events",
        sa.Column(
            "id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("decision_type", sa.String(40), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=True),
        sa.Column(
            "proposal_id",
            sa.UUID(),
            sa.ForeignKey("schema_proposals.id"),
            nullable=True,
        ),
        sa.Column("schema_version_id", sa.UUID(), nullable=True),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.Column("trust_score_at_time", sa.Float(), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_index(
        "ix_governance_decision_events_type_time",
        "governance_decision_events",
        ["decision_type", sa.text("recorded_at DESC")],
    )

    # Strict append-only: no DELETE.
    op.execute("""
        CREATE OR REPLACE FUNCTION governance_decision_events_no_delete()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'governance_decision_events is append-only: DELETE is forbidden'
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_governance_decision_events_no_delete
        BEFORE DELETE ON governance_decision_events
        FOR EACH ROW EXECUTE FUNCTION governance_decision_events_no_delete();
    """)

    # Strict append-only: no UPDATE.
    op.execute("""
        CREATE OR REPLACE FUNCTION governance_decision_events_no_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'governance_decision_events is append-only: UPDATE is forbidden'
                USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_governance_decision_events_no_update
        BEFORE UPDATE ON governance_decision_events
        FOR EACH ROW EXECUTE FUNCTION governance_decision_events_no_update();
    """)

    # D167 read-only role grant (conditional — role may not exist in dev).
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
                EXECUTE 'GRANT SELECT ON governance_decision_events TO grace_readonly';
            END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_governance_decision_events_no_update "
        "ON governance_decision_events"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS governance_decision_events_no_update()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_governance_decision_events_no_delete "
        "ON governance_decision_events"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS governance_decision_events_no_delete()"
    )
    op.drop_table("governance_decision_events")
