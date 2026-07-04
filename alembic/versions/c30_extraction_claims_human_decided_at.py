"""add human_decided_at to extraction_claims

Chunk 30, D230. NULL-permissible TIMESTAMP column. Per-claim writer sets
this atomically with decision_source='human' on accept or reject. No
backfill; historical claims (decision_source='auto') remain NULL. No
indexes — existing indexes on status/verdict cover the list-filtering
use case.

Revision ID: c30_extraction_claims_human_decided_at
Revises: c29b_cq_candidates
Create Date: 2026-05-01 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# NOTE: alembic_version.version_num is VARCHAR(32); revision id must fit.
revision: str = "c30_human_decided_at"
down_revision: Union[str, Sequence[str], None] = "c29b_cq_candidates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_claims",
        sa.Column("human_decided_at", sa.DateTime(timezone=False), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_claims", "human_decided_at")
