"""create recon_documented_reality_schedules table

Chunk 37, D287. Persists Documented Reality Report scheduling rows
loaded by the FastAPI lifespan APScheduler. Cadence enum is
``quarterly`` / ``monthly`` / ``on_demand`` (snake_case; spec §18 #7
locked).

Creates:
  - recon_documented_reality_schedules table — additive; no
    destructive ALTER.
  - GRANT SELECT to grace_readonly (D167).

Revision ID: c37a_recon_dr_schedules
Revises: c36c_ontology_segment_id
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c37a_recon_dr_schedules"
down_revision: Union[str, Sequence[str], None] = "c36c_ontology_segment_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "recon_documented_reality_schedules",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("cadence", sa.Text(), nullable=False),
        sa.Column(
            "next_run_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "metadata_extra",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "cadence IN ('quarterly','monthly','on_demand')",
            name="ck_recon_documented_reality_schedules_cadence",
        ),
    )

    # D167: grace_readonly Postgres role used by Grafana datasource.
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON recon_documented_reality_schedules TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.drop_table("recon_documented_reality_schedules")
