"""partial unique: one running hypothesis run per evidence_id (Chunk 42, DV4)

Revision ID: c42d_hypothesis_one_running
Revises: c42c_permission_hypothesis_runs

Note: the original revision id `c42d_hypothesis_one_running_per_evidence`
(41 chars) overflowed `alembic_version.version_num VARCHAR(32)`, blocking
`alembic upgrade head`. Renamed to `c42d_hypothesis_one_running` (27 chars)
during chunk-43 code-stage resume per architect-approved remediation A
(2026-05-09). The partial unique index name + DV4 contract are unchanged.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c42d_hypothesis_one_running"
down_revision: Union[str, Sequence[str], None] = "c42c_permission_hypothesis_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_permission_hypothesis_runs_one_running_evidence",
        "permission_hypothesis_runs",
        ["evidence_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_permission_hypothesis_runs_one_running_evidence",
        table_name="permission_hypothesis_runs",
    )
