"""F-57: prune_voice_tone_versions must delete recipient_style_profiles
children before pruning parent communication_style_profiles rows.

validation run (2026-07-01): the retention prune (retention_versions=4)
violated fk_rsp_profile_id — children were not deleted before their parent
profile versions, so voice profiling ABORTED mid-run once any sender exceeded
retention. This CREATE OR REPLACE captures the versions to prune into an array,
deletes the recipient_style_profiles children first, then the parents.

Revision id kept <= 32 chars (D350 alembic_version.version_num VARCHAR(32)).
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f57_prune_children_first"
down_revision = "d535_diag_pattern_6th"
branch_labels = None
depends_on = None


_FIXED_FN = r"""
CREATE OR REPLACE FUNCTION public.prune_voice_tone_versions(
    p_sender_id uuid, p_aggregate_segment text, p_keep_n integer
)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    deleted_count INT;
    del_ids uuid[];
BEGIN
    -- R3: prevent concurrent race (Chunk 58 spec §13)
    LOCK TABLE communication_style_profiles IN EXCLUSIVE MODE;

    -- Enable bypass for append-only trigger (transaction-scoped via SET LOCAL)
    SET LOCAL app.voice_tone_prune = 'true';

    -- Identify the parent versions to prune (oldest beyond p_keep_n).
    SELECT array_agg(id) INTO del_ids
    FROM (
        SELECT id, ROW_NUMBER() OVER (ORDER BY profile_version DESC) AS rn
        FROM communication_style_profiles
        WHERE (p_sender_id IS NOT NULL AND sender_person_id = p_sender_id)
           OR (p_aggregate_segment IS NOT NULL AND aggregate_segment = p_aggregate_segment)
    ) ranked
    WHERE rn > p_keep_n;

    IF del_ids IS NULL THEN
        SET LOCAL app.voice_tone_prune = 'false';
        RETURN 0;
    END IF;

    -- F-57: delete FK children FIRST so the parent DELETE does not violate
    -- fk_rsp_profile_id (recipient_style_profiles.profile_id -> profiles.id).
    DELETE FROM recipient_style_profiles WHERE profile_id = ANY(del_ids);

    -- Then delete the parent profile versions.
    DELETE FROM communication_style_profiles WHERE id = ANY(del_ids);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;

    SET LOCAL app.voice_tone_prune = 'false';
    RETURN deleted_count;
END;
$function$
"""

# The pre-F-57 definition (no child delete) — restored on downgrade.
_ORIGINAL_FN = r"""
CREATE OR REPLACE FUNCTION public.prune_voice_tone_versions(
    p_sender_id uuid, p_aggregate_segment text, p_keep_n integer
)
 RETURNS integer
 LANGUAGE plpgsql
 SECURITY DEFINER
AS $function$
DECLARE
    deleted_count INT;
BEGIN
    LOCK TABLE communication_style_profiles IN EXCLUSIVE MODE;
    SET LOCAL app.voice_tone_prune = 'true';
    WITH ranked AS (
        SELECT id, ROW_NUMBER() OVER (ORDER BY profile_version DESC) AS rn
        FROM communication_style_profiles
        WHERE (p_sender_id IS NOT NULL AND sender_person_id = p_sender_id)
           OR (p_aggregate_segment IS NOT NULL AND aggregate_segment = p_aggregate_segment)
    )
    DELETE FROM communication_style_profiles
    WHERE id IN (SELECT id FROM ranked WHERE rn > p_keep_n);
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    SET LOCAL app.voice_tone_prune = 'false';
    RETURN deleted_count;
END;
$function$
"""


def upgrade() -> None:
    op.execute(_FIXED_FN)


def downgrade() -> None:
    op.execute(_ORIGINAL_FN)
