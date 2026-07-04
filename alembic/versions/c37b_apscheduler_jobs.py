"""create apscheduler_jobs table

Chunk 37, D287. APScheduler 3.x ``SQLAlchemyJobStore`` table shape;
explicit Alembic migration so CI audits the schema. APScheduler
runtime is initialized with ``create_tables=False`` so this migration
is the sole schema authority.

Revision ID: c37b_apscheduler_jobs
Revises: c37a_recon_documented_reality_schedules
Create Date: 2026-05-07 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c37b_apscheduler_jobs"
down_revision: Union[str, Sequence[str], None] = "c37a_recon_dr_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apscheduler_jobs",
        sa.Column("id", sa.String(length=191), nullable=False),
        sa.Column("next_run_time", sa.Float(precision=53), nullable=True),
        sa.Column("job_state", sa.LargeBinary(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_apscheduler_jobs_next_run_time",
        "apscheduler_jobs",
        ["next_run_time"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_apscheduler_jobs_next_run_time",
        table_name="apscheduler_jobs",
    )
    op.drop_table("apscheduler_jobs")
