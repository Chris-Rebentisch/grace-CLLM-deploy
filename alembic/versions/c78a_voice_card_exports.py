# D506 — Voice Card export audit trail.
# Append-only table recording every Voice Card export for governance audit.
#
# Invariant carve-out: Alembic head change.
# (1) Invariant: Alembic migration chain.
# (2) Carve-out: new c78a_voice_card_exports migration.
# (3) Authorization: D506 / chunk-78-spec-v4-FINAL.md §4.

"""c78a: voice card export audit trail (D506)

Revision ID: c78a_voice_card_exports
Revises: c77a_image_jobs
Create Date: 2026-05-28
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c78a_voice_card_exports"
down_revision: str = "c77a_image_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "voice_card_exports",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("redaction_applied", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("operator", sa.Text(), nullable=True),
        sa.Column("exported_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # Append-only trigger — no UPDATE or DELETE allowed.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_voice_card_exports_immutable()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'voice_card_exports rows are immutable — UPDATE forbidden';
            ELSIF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'voice_card_exports rows are immutable — DELETE forbidden';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        CREATE TRIGGER voice_card_exports_immutable
        BEFORE UPDATE OR DELETE ON voice_card_exports
        FOR EACH ROW EXECUTE FUNCTION trg_voice_card_exports_immutable();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS voice_card_exports_immutable ON voice_card_exports")
    op.execute("DROP FUNCTION IF EXISTS trg_voice_card_exports_immutable()")
    op.drop_table("voice_card_exports")
