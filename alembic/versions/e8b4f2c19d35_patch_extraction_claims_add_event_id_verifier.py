"""patch extraction_claims: add extraction_event_id and verifier_model

Revision ID: e8b4f2c19d35
Revises: d7a3e1f09b24
Create Date: 2026-04-09 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e8b4f2c19d35"
down_revision: Union[str, None] = "d7a3e1f09b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "extraction_claims",
        sa.Column("extraction_event_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "extraction_claims",
        sa.Column("verifier_model", sa.String(100), nullable=True),
    )
    op.create_index(
        "ix_extraction_claims_extraction_event_id",
        "extraction_claims",
        ["extraction_event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_extraction_claims_extraction_event_id", table_name="extraction_claims")
    op.drop_column("extraction_claims", "verifier_model")
    op.drop_column("extraction_claims", "extraction_event_id")
