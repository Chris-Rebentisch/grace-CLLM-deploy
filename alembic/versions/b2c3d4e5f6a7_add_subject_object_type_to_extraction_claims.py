"""add subject_type and object_type to extraction_claims

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-10

"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_claims",
        sa.Column("subject_type", sa.String(100), nullable=True),
    )
    op.add_column(
        "extraction_claims",
        sa.Column("object_type", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extraction_claims", "object_type")
    op.drop_column("extraction_claims", "subject_type")
