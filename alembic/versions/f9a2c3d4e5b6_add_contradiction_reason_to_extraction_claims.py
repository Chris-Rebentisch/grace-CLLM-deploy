"""add contradiction_reason to extraction_claims

Revision ID: f9a2c3d4e5b6
Revises: e8b4f2c19d35
Create Date: 2026-04-09

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "f9a2c3d4e5b6"
down_revision = "e8b4f2c19d35"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_claims",
        sa.Column("contradiction_reason", sa.String(2000), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_claims", "contradiction_reason")
