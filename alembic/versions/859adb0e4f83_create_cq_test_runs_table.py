"""create cq_test_runs table

Revision ID: 859adb0e4f83
Revises: 896c59ca3fc6
Create Date: 2026-03-30 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '859adb0e4f83'
down_revision: Union[str, Sequence[str], None] = '896c59ca3fc6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create cq_test_runs table + indexes."""
    op.create_table(
        'cq_test_runs',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('schema_version_id', sa.UUID(), nullable=False),
        sa.Column('schema_version_number', sa.Integer(), nullable=True),
        sa.Column('is_proposed_schema', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('proposed_schema_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('total_cqs', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('passing', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('failing', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('out_of_scope', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('errors', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('pass_rate', sa.Float(), nullable=False, server_default=sa.text('0.0')),
        sa.Column('status', sa.String(length=20), nullable=False, server_default=sa.text("'running'")),
        sa.Column('model', sa.Text(), nullable=True),
        sa.Column('provider', sa.Text(), nullable=True),
        sa.Column('concurrency', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('results_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('gap_summary', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.Column('duration_ms', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('metadata_extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['schema_version_id'], ['ontology_versions.id'], name='fk_cq_test_runs_schema_version'),
    )
    op.create_index('ix_cq_test_runs_schema_version_id', 'cq_test_runs', ['schema_version_id'])
    op.create_index('ix_cq_test_runs_status', 'cq_test_runs', ['status'])
    op.create_index('ix_cq_test_runs_created_at', 'cq_test_runs', ['created_at'])


def downgrade() -> None:
    """Drop cq_test_runs table."""
    op.drop_table('cq_test_runs')
