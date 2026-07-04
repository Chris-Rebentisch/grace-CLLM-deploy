"""Make schema_proposals.raw_confidence nullable (F-0042 / ISS-0053 deferral).

Capture-the-why (F-0042 / ISS-0053, validation run 2026-07-03):
operator-authored proposals from ``POST /api/ontology/proposals`` had to
persist a fabricated ``raw_confidence = 1.0`` because the column was
``NOT NULL`` — documented as an interim in the ISS-0053 fix round. Per
D120/D217 discipline, numeric confidence must never be fabricated: a
human-initiated / signal-less proposal has NO agent confidence, so the
honest value is NULL. This migration drops the NOT NULL constraint;
the create route and Pydantic model now store/accept ``None``.

Trigger interaction (verified against c50a + c65b): the
``schema_proposals_append_only()`` trigger guards ``raw_confidence``
UPDATEs via ``OLD.raw_confidence IS DISTINCT FROM NEW.raw_confidence``,
which is NULL-safe — no trigger change is required to admit NULL values
at INSERT or keep them immutable afterward. The append-only invariant
(Chunk 47, widened Chunk 50/65) is untouched.

Scope note: ``calibration_decisions.raw_confidence`` (c49a) stays
NOT NULL — calibration rows are only recorded for signal-backed agent
confidence (the decide route now skips signal-less proposals).

Revision ID: r4a_raw_confidence_nullable
Revises: f49a_ns_readiness
Create Date: 2026-07-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic. (<=32 chars per D350)
revision: str = "r4a_raw_confidence_nullable"
down_revision: Union[str, Sequence[str], None] = "f49a_ns_readiness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "schema_proposals",
        "raw_confidence",
        existing_type=sa.Float(),
        nullable=True,
    )


def downgrade() -> None:
    # Backfill NULLs to the pre-fix human-initiated sentinel (1.0) before
    # re-imposing NOT NULL, so the downgrade never fails on rows written
    # under the nullable schema. This reintroduces the fabricated value
    # F-0042 flagged — acceptable only as a downgrade compatibility shim.
    op.execute(
        "UPDATE schema_proposals SET raw_confidence = 1.0 WHERE raw_confidence IS NULL"
    )
    op.alter_column(
        "schema_proposals",
        "raw_confidence",
        existing_type=sa.Float(),
        nullable=False,
    )
