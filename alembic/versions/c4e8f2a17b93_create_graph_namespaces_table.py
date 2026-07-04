"""create graph_namespaces table

Revision ID: c4e8f2a17b93
Revises: a3b7c9d1e2f4
Create Date: 2026-04-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c4e8f2a17b93'
down_revision: Union[str, None] = 'a3b7c9d1e2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'graph_namespaces',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('database_name', sa.String(), unique=True, nullable=False),
        sa.Column('description', sa.String(), server_default=''),
        sa.Column('parent_database', sa.String(), server_default='grace'),
        sa.Column('is_mother', sa.Boolean(), server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sync_status', sa.String(), server_default='never_synced'),
        sa.Column('metadata', postgresql.JSONB(), server_default='{}'),
    )


def downgrade() -> None:
    op.drop_table('graph_namespaces')
