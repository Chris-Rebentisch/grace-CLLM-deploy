"""create recon_documented_reality_reports table

Chunk 37, D286/D287. Persists Documented Reality Reports for the
``GET /api/recon/documented-reality/{report_id}`` and ``.../latest``
read paths. The spec §3.2 enumerates three migrations (a/b/c); this
fourth migration is the additive persistence backing for the GET
routes called out in §6 CP6 ("get-latest happy + 404"). No
``gap_reports`` table changes (D280 preserved verbatim); no other
existing column mutated.

Creates:
  - recon_documented_reality_reports table — additive; no destructive
    ALTER.
  - GRANT SELECT to grace_readonly (D167).

Revision ID: c37d_recon_dr_reports
Revises: c37c_recon_divergence_maps
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c37d_recon_dr_reports"
down_revision: Union[str, Sequence[str], None] = "c37c_recon_divergence_maps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recon_documented_reality_reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("trigger", sa.Text(), nullable=False),
        sa.Column(
            "corpus_below_floor",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "report_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "trigger IN ('scheduled','on_demand')",
            name="ck_recon_documented_reality_reports_trigger",
        ),
    )

    op.create_index(
        "ix_recon_documented_reality_reports_generated_at",
        "recon_documented_reality_reports",
        [sa.text("generated_at DESC")],
    )

    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON recon_documented_reality_reports TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recon_documented_reality_reports_generated_at",
        table_name="recon_documented_reality_reports",
    )
    op.drop_table("recon_documented_reality_reports")
