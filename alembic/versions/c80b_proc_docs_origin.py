"""D518 — additive `origin` + `source_type` columns on processed_documents
for email-origin row discrimination.

D356 capture-the-why: D518 — `origin` + `source_type` additive columns for
email-origin row discrimination in processed_documents.

Revision ID: c80b_proc_docs_origin
Revises: c80a_thread_position
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c80b_proc_docs_origin"
down_revision: str | Sequence[str] | None = "c80a_thread_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("processed_documents", sa.Column("origin", sa.Text(), nullable=True))
    op.add_column("processed_documents", sa.Column("source_type", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("processed_documents", "source_type")
    op.drop_column("processed_documents", "origin")
