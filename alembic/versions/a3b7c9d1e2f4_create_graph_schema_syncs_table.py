"""create graph_schema_syncs table

Revision ID: a3b7c9d1e2f4
Revises: 859adb0e4f83
Create Date: 2026-04-02 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3b7c9d1e2f4'
down_revision: Union[str, None] = '859adb0e4f83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'graph_schema_syncs',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('ontology_version_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('ontology_version_number', sa.Integer(), nullable=False),
        sa.Column('ddl_statements', postgresql.JSONB(), nullable=False),
        sa.Column('total_statements', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('succeeded', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('failed', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('graph_schema_syncs')
