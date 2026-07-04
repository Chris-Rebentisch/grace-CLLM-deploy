"""create ontology management tables

Revision ID: 2f29ad55b099
Revises: 658d756882d4
Create Date: 2026-03-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '2f29ad55b099'
down_revision: Union[str, Sequence[str], None] = '658d756882d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ontology_versions, schema_proposals, calibration_records, schema_promotion_events tables + trigger."""

    # 1. ontology_versions (self-referencing FK, circular FK to schema_proposals via use_alter)
    op.create_table(
        'ontology_versions',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('version_number', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('schema_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('schema_modules', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('patch_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('diff_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('previous_version_id', sa.UUID(), nullable=True),
        sa.Column('hash_chain', sa.Text(), nullable=False),
        sa.Column('source', sa.String(length=30), nullable=False),
        sa.Column('proposal_id', sa.UUID(), nullable=True),
        sa.Column('reviewer', sa.Text(), nullable=True),
        sa.Column('changelog', sa.Text(), nullable=True),
        sa.Column('kgcl_commands', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('cq_coverage_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('entity_type_count', sa.Integer(), nullable=True),
        sa.Column('relationship_type_count', sa.Integer(), nullable=True),
        sa.Column('promotion_gate_passed', sa.Boolean(), nullable=True),
        sa.Column('promotion_gate_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('metadata_extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['previous_version_id'], ['ontology_versions.id'], name='fk_ontology_versions_previous'),
        sa.CheckConstraint("jsonb_typeof(schema_json) = 'object'", name='ck_ontology_versions_schema_json_is_object'),
    )
    op.create_index('ix_ontology_versions_version_number', 'ontology_versions', ['version_number'], unique=True)
    op.create_index('ix_ontology_versions_is_active', 'ontology_versions', ['is_active'], postgresql_where=sa.text('is_active = TRUE'))
    op.create_index('ix_ontology_versions_created_at', 'ontology_versions', ['created_at'])
    op.create_index('ix_ontology_versions_source', 'ontology_versions', ['source'])

    # 2. schema_proposals (FKs to ontology_versions)
    op.create_table(
        'schema_proposals',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('proposal_type', sa.String(length=30), nullable=False),
        sa.Column('change_tier', sa.Integer(), nullable=False),
        sa.Column('kgcl_command', sa.Text(), nullable=False),
        sa.Column('proposed_diff', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('signal_type', sa.String(length=20), nullable=True),
        sa.Column('raw_confidence', sa.Float(), nullable=False),
        sa.Column('priority', sa.String(length=10), nullable=False, server_default=sa.text("'medium'")),
        sa.Column('status', sa.String(length=20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('current_schema_version_id', sa.UUID(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewer', sa.Text(), nullable=True),
        sa.Column('human_decision', sa.String(length=20), nullable=True),
        sa.Column('modification_distance', sa.Float(), nullable=True),
        sa.Column('modified_diff', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('applied_autonomously', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('autonomy_confidence_at_time', sa.Float(), nullable=True),
        sa.Column('trust_score_at_time', sa.Float(), nullable=True),
        sa.Column('resulting_version_id', sa.UUID(), nullable=True),
        sa.Column('cooling_period_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cooling_period_reverted', sa.Boolean(), nullable=True),
        sa.Column('metadata_extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb")),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['current_schema_version_id'], ['ontology_versions.id'], name='fk_schema_proposals_current_version'),
    )
    op.create_index('ix_schema_proposals_status', 'schema_proposals', ['status'])
    op.create_index('ix_schema_proposals_change_tier', 'schema_proposals', ['change_tier'])
    op.create_index('ix_schema_proposals_signal_type', 'schema_proposals', ['signal_type'])
    op.create_index('ix_schema_proposals_created_at', 'schema_proposals', ['created_at'])
    op.create_index('ix_schema_proposals_current_schema_version_id', 'schema_proposals', ['current_schema_version_id'])

    # Circular FKs (use_alter equivalent — add after both tables exist)
    op.create_foreign_key(
        'fk_ontology_versions_proposal',
        'ontology_versions', 'schema_proposals',
        ['proposal_id'], ['id'],
    )
    op.create_foreign_key(
        'fk_schema_proposals_resulting_version',
        'schema_proposals', 'ontology_versions',
        ['resulting_version_id'], ['id'],
    )

    # 3. calibration_records (no FKs to other new tables)
    op.create_table(
        'calibration_records',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('change_tier', sa.Integer(), nullable=False),
        sa.Column('confidence_band_low', sa.Float(), nullable=False),
        sa.Column('confidence_band_high', sa.Float(), nullable=False),
        sa.Column('approval_rate', sa.Float(), nullable=False),
        sa.Column('sample_count', sa.Integer(), nullable=False),
        sa.Column('trust_score', sa.Float(), nullable=False),
        sa.Column('autonomy_threshold', sa.Float(), nullable=False),
        sa.Column('autonomy_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('window_size', sa.Integer(), nullable=False, server_default=sa.text('50')),
        sa.Column('risk_tolerance', sa.Float(), nullable=False, server_default=sa.text('0.95')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_calibration_records_tier_computed', 'calibration_records', ['change_tier', sa.text('computed_at DESC')])

    # 4. schema_promotion_events (FKs to schema_proposals and ontology_versions)
    op.create_table(
        'schema_promotion_events',
        sa.Column('id', sa.UUID(), nullable=False, default=sa.text('gen_random_uuid()')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('proposal_id', sa.UUID(), nullable=False),
        sa.Column('schema_version_before_id', sa.UUID(), nullable=False),
        sa.Column('proposed_schema_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('cq_pass_rate', sa.Float(), nullable=True),
        sa.Column('cq_total', sa.Integer(), nullable=True),
        sa.Column('cq_passing', sa.Integer(), nullable=True),
        sa.Column('mine1_retention', sa.Float(), nullable=True),
        sa.Column('mine1_sample_size', sa.Integer(), nullable=True),
        sa.Column('gate_passed', sa.Boolean(), nullable=False),
        sa.Column('gate_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['proposal_id'], ['schema_proposals.id'], name='fk_promotion_events_proposal'),
        sa.ForeignKeyConstraint(['schema_version_before_id'], ['ontology_versions.id'], name='fk_promotion_events_version_before'),
    )
    op.create_index('ix_promotion_events_proposal_id', 'schema_promotion_events', ['proposal_id'])

    # 5. Append-only trigger on ontology_versions
    op.execute("""
CREATE OR REPLACE FUNCTION prevent_ontology_version_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'ontology_versions table is append-only. Deletes are not permitted.';
    END IF;
    IF TG_OP = 'UPDATE' THEN
        -- Allow UPDATE only on is_active column
        IF (OLD.version_number IS DISTINCT FROM NEW.version_number) OR
           (OLD.schema_json IS DISTINCT FROM NEW.schema_json) OR
           (OLD.hash_chain IS DISTINCT FROM NEW.hash_chain) OR
           (OLD.source IS DISTINCT FROM NEW.source) OR
           (OLD.created_at IS DISTINCT FROM NEW.created_at) OR
           (OLD.reviewer IS DISTINCT FROM NEW.reviewer) OR
           (OLD.changelog IS DISTINCT FROM NEW.changelog) OR
           (OLD.patch_json IS DISTINCT FROM NEW.patch_json) OR
           (OLD.diff_summary IS DISTINCT FROM NEW.diff_summary) OR
           (OLD.previous_version_id IS DISTINCT FROM NEW.previous_version_id) OR
           (OLD.proposal_id IS DISTINCT FROM NEW.proposal_id) OR
           (OLD.kgcl_commands IS DISTINCT FROM NEW.kgcl_commands) OR
           (OLD.cq_coverage_snapshot IS DISTINCT FROM NEW.cq_coverage_snapshot) OR
           (OLD.entity_type_count IS DISTINCT FROM NEW.entity_type_count) OR
           (OLD.relationship_type_count IS DISTINCT FROM NEW.relationship_type_count) OR
           (OLD.promotion_gate_passed IS DISTINCT FROM NEW.promotion_gate_passed) OR
           (OLD.promotion_gate_details IS DISTINCT FROM NEW.promotion_gate_details) OR
           (OLD.schema_modules IS DISTINCT FROM NEW.schema_modules) OR
           (OLD.metadata_extra IS DISTINCT FROM NEW.metadata_extra) THEN
            RAISE EXCEPTION 'ontology_versions table is append-only. Only is_active may be updated.';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
""")

    op.execute("""
CREATE TRIGGER trig_ontology_versions_immutable
    BEFORE UPDATE OR DELETE ON ontology_versions
    FOR EACH ROW
    EXECUTE FUNCTION prevent_ontology_version_mutation();
""")


def downgrade() -> None:
    """Drop trigger, function, and all 4 ontology management tables."""
    op.execute("DROP TRIGGER IF EXISTS trig_ontology_versions_immutable ON ontology_versions;")
    op.execute("DROP FUNCTION IF EXISTS prevent_ontology_version_mutation();")
    op.drop_table('schema_promotion_events')
    op.drop_table('calibration_records')
    # Drop circular FKs before dropping tables
    op.drop_constraint('fk_schema_proposals_resulting_version', 'schema_proposals', type_='foreignkey')
    op.drop_constraint('fk_ontology_versions_proposal', 'ontology_versions', type_='foreignkey')
    op.drop_table('schema_proposals')
    op.drop_table('ontology_versions')
