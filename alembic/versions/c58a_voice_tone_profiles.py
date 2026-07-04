"""Voice & Tone Profiling tables + SECURITY DEFINER retention (Chunk 58, D423)

DDL target 1 — ``communication_style_profiles``:
  Append-only trigger ``trg_communication_style_profiles_guard`` with SECURITY
  DEFINER retention bypass via ``app.voice_tone_prune`` session variable.

DDL target 2 — ``recipient_style_profiles``:
  Append-only trigger ``trg_recipient_style_profiles_guard`` (same bypass variable).

DDL target 3 — ``department_communication_profiles`` VIEW:
  Aggregate-only subset of ``communication_style_profiles``.

SECURITY DEFINER function ``prune_voice_tone_versions``:
  LOCK TABLE to prevent concurrent race (R3 mitigation).
  SET LOCAL app.voice_tone_prune = 'true' for bypass.

Invariant: append-only. Carve-out: SECURITY DEFINER ``prune_voice_tone_versions``
uses ``SET LOCAL app.voice_tone_prune = 'true'`` for version retention.
Authorization: D423.

Revision ID: c58a_voice_tone_profiles
Revises: c57a_ingest_chk_apscheduler
Create Date: 2026-05-19
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c58a_voice_tone_profiles"
down_revision: Union[str, Sequence[str], None] = "c57a_ingest_chk_apscheduler"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # DDL target 1 — communication_style_profiles
    # -----------------------------------------------------------------------
    op.create_table(
        "communication_style_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sender_person_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "aggregate_segment",
            sa.Text(),
            nullable=True,
        ),
        sa.Column(
            "profile_version",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "style_signature",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "profile_quality_band",
            sa.String(10),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        # Mutual-exclusion CHECK: exactly one of sender_person_id or aggregate_segment
        sa.CheckConstraint(
            "(sender_person_id IS NULL) <> (aggregate_segment IS NULL)",
            name="ck_csp_sender_xor_aggregate",
        ),
        sa.CheckConstraint(
            "profile_quality_band IN ('high','medium','low')",
            name="ck_csp_quality_band",
        ),
    )

    # Unique on (COALESCE(sender_person_id::text, aggregate_segment), profile_version)
    op.execute(
        """
CREATE UNIQUE INDEX uq_csp_identity_version
ON communication_style_profiles (
    COALESCE(sender_person_id::text, aggregate_segment),
    profile_version
);
"""
    )

    # Append-only trigger with SECURITY DEFINER bypass
    op.execute(
        """
CREATE OR REPLACE FUNCTION communication_style_profiles_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    -- SECURITY DEFINER retention bypass via session variable (D423)
    IF current_setting('app.voice_tone_prune', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'communication_style_profiles is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_communication_style_profiles_guard
BEFORE UPDATE OR DELETE ON communication_style_profiles
FOR EACH ROW EXECUTE FUNCTION communication_style_profiles_guard();
"""
    )

    # -----------------------------------------------------------------------
    # DDL target 2 — recipient_style_profiles
    # -----------------------------------------------------------------------
    op.create_table(
        "recipient_style_profiles",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "profile_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "recipient_person_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "category",
            sa.String(30),
            nullable=False,
        ),
        sa.Column(
            "confidence_band",
            sa.String(10),
            nullable=False,
        ),
        sa.Column(
            "style_delta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["communication_style_profiles.id"],
            name="fk_rsp_profile_id",
        ),
        sa.CheckConstraint(
            "confidence_band IN ('high','medium','low')",
            name="ck_rsp_confidence_band",
        ),
    )

    # Append-only trigger (same bypass variable)
    op.execute(
        """
CREATE OR REPLACE FUNCTION recipient_style_profiles_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('alembic.downgrading', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    IF current_setting('app.voice_tone_prune', true) = 'true' THEN
        RETURN COALESCE(NEW, OLD);
    END IF;
    RAISE EXCEPTION
        'recipient_style_profiles is append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql;
"""
    )
    op.execute(
        """
CREATE TRIGGER trg_recipient_style_profiles_guard
BEFORE UPDATE OR DELETE ON recipient_style_profiles
FOR EACH ROW EXECUTE FUNCTION recipient_style_profiles_guard();
"""
    )

    # -----------------------------------------------------------------------
    # DDL target 3 — department_communication_profiles VIEW
    # -----------------------------------------------------------------------
    op.execute(
        """
CREATE VIEW department_communication_profiles AS
SELECT * FROM communication_style_profiles WHERE sender_person_id IS NULL;
"""
    )

    # -----------------------------------------------------------------------
    # SECURITY DEFINER retention function (R3 mitigation: LOCK TABLE)
    # -----------------------------------------------------------------------
    op.execute(
        """
CREATE OR REPLACE FUNCTION prune_voice_tone_versions(
    p_sender_id UUID,
    p_aggregate_segment TEXT,
    p_keep_n INT
)
RETURNS INT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    deleted_count INT;
BEGIN
    -- R3: prevent concurrent race (Chunk 58 spec §13)
    LOCK TABLE communication_style_profiles IN EXCLUSIVE MODE;

    -- Enable bypass for append-only trigger (transaction-scoped via SET LOCAL)
    SET LOCAL app.voice_tone_prune = 'true';

    -- Delete oldest versions beyond p_keep_n
    WITH ranked AS (
        SELECT id, ROW_NUMBER() OVER (ORDER BY profile_version DESC) AS rn
        FROM communication_style_profiles
        WHERE (p_sender_id IS NOT NULL AND sender_person_id = p_sender_id)
           OR (p_aggregate_segment IS NOT NULL AND aggregate_segment = p_aggregate_segment)
    )
    DELETE FROM communication_style_profiles
    WHERE id IN (SELECT id FROM ranked WHERE rn > p_keep_n);

    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    -- Reset bypass variable (will also reset at transaction end via SET LOCAL)
    SET LOCAL app.voice_tone_prune = 'false';

    RETURN deleted_count;
END;
$$;
"""
    )

    # -----------------------------------------------------------------------
    # GRANT SELECT to grace_readonly (D167)
    # -----------------------------------------------------------------------
    op.execute(
        """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'grace_readonly') THEN
        EXECUTE 'GRANT SELECT ON communication_style_profiles TO grace_readonly';
        EXECUTE 'GRANT SELECT ON recipient_style_profiles TO grace_readonly';
        EXECUTE 'GRANT SELECT ON department_communication_profiles TO grace_readonly';
    END IF;
END
$$;
"""
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS department_communication_profiles")
    op.execute("DROP FUNCTION IF EXISTS prune_voice_tone_versions(UUID, TEXT, INT)")
    op.execute("DROP TRIGGER IF EXISTS trg_recipient_style_profiles_guard ON recipient_style_profiles")
    op.execute("DROP FUNCTION IF EXISTS recipient_style_profiles_guard()")
    op.drop_table("recipient_style_profiles")
    op.execute("DROP TRIGGER IF EXISTS trg_communication_style_profiles_guard ON communication_style_profiles")
    op.execute("DROP FUNCTION IF EXISTS communication_style_profiles_guard()")
    op.drop_table("communication_style_profiles")
