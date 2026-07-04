"""create review session tables

Revision ID: 896c59ca3fc6
Revises: 2f29ad55b099
Create Date: 2026-03-30 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '896c59ca3fc6'
down_revision: Union[str, Sequence[str], None] = '2f29ad55b099'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create review_sessions, review_decisions, change_of_status_events tables + indexes."""

    # 1. review_sessions (FK to ontology_versions via resulting_version_id)
    op.create_table(
        'review_sessions',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default=sa.text("'in_progress'")),
        sa.Column('reviewer', sa.Text(), nullable=False),
        sa.Column('seed_schema_merge_run_id', sa.Text(), nullable=False),
        sa.Column('seed_schema_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('total_entity_types', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_relationships', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('reviewed_entity_types', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('reviewed_relationships', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('resulting_version_id', sa.UUID(), nullable=True),
        sa.Column('metadata_extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['resulting_version_id'], ['ontology_versions.id'], name='fk_review_sessions_resulting_version'),
    )
    op.create_index('ix_review_sessions_status', 'review_sessions', ['status'])
    op.create_index('ix_review_sessions_created_at', 'review_sessions', ['created_at'])

    # 2. review_decisions (FK to review_sessions)
    op.create_table(
        'review_decisions',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('element_type', sa.String(length=20), nullable=False),
        sa.Column('element_name', sa.Text(), nullable=False),
        sa.Column('decision', sa.String(length=20), nullable=False),
        sa.Column('original_data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('modified_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('split_into', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('merged_with', sa.Text(), nullable=True),
        sa.Column('reviewer', sa.Text(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('cq_impact', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('metadata_extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['session_id'], ['review_sessions.id'], name='fk_review_decisions_session'),
    )
    op.create_index('ix_review_decisions_session_id', 'review_decisions', ['session_id'])
    op.create_index('ix_review_decisions_element_type', 'review_decisions', ['element_type'])
    op.create_index('ix_review_decisions_element_name', 'review_decisions', ['element_name'])
    op.create_index('ix_review_decisions_decision', 'review_decisions', ['decision'])

    # 3. change_of_status_events (no FKs — entity_id is a generic UUID reference)
    op.create_table(
        'change_of_status_events',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('entity_type', sa.String(length=30), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=False),
        sa.Column('from_status', sa.Text(), nullable=False),
        sa.Column('to_status', sa.Text(), nullable=False),
        sa.Column('agent', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata_extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_change_of_status_entity_id', 'change_of_status_events', ['entity_id'])
    op.create_index('ix_change_of_status_entity_type', 'change_of_status_events', ['entity_type'])
    op.create_index('ix_change_of_status_created_at', 'change_of_status_events', ['created_at'])


def downgrade() -> None:
    """Drop review session tables in reverse FK order."""
    op.drop_table('change_of_status_events')
    op.drop_table('review_decisions')
    op.drop_table('review_sessions')
