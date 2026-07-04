"""F-57 regression: prune_voice_tone_versions must delete recipient_style_profiles
children before pruning parent profile versions, or fk_rsp_profile_id aborts the
run once a sender exceeds retention_versions. All writes are rolled back."""

from __future__ import annotations

import uuid

from sqlalchemy import text

from src.shared.database import get_session_factory


def test_prune_deletes_children_before_parents():
    session = get_session_factory()()
    try:
        sender = uuid.uuid4()
        # Two parent versions for the sender.
        ids = []
        for v in (1, 2):
            pid = uuid.uuid4()
            ids.append(pid)
            session.execute(
                text(
                    "INSERT INTO communication_style_profiles "
                    "(id, sender_person_id, profile_version, style_signature, profile_quality_band) "
                    "VALUES (:id, :s, :v, '{}'::jsonb, 'medium')"
                ),
                {"id": pid, "s": sender, "v": v},
            )
        # A recipient child on the OLDEST version (v1) — the one prune removes.
        session.execute(
            text(
                "INSERT INTO recipient_style_profiles "
                "(profile_id, recipient_person_id, category, confidence_band, style_delta) "
                "VALUES (:pid, :r, 'peer', 'medium', '{}'::jsonb)"
            ),
            {"pid": ids[0], "r": uuid.uuid4()},
        )
        session.flush()

        # keep_n=1 → prune the oldest version. Must NOT raise fk_rsp_profile_id.
        deleted = session.execute(
            text("SELECT prune_voice_tone_versions(:s, NULL, 1)"),
            {"s": sender},
        ).scalar()
        assert deleted == 1

        # The child of the pruned parent is gone.
        remaining_children = session.execute(
            text("SELECT count(*) FROM recipient_style_profiles WHERE profile_id = :pid"),
            {"pid": ids[0]},
        ).scalar()
        assert remaining_children == 0
        # The kept parent version survives.
        kept = session.execute(
            text("SELECT count(*) FROM communication_style_profiles WHERE sender_person_id = :s"),
            {"s": sender},
        ).scalar()
        assert kept == 1
    finally:
        session.rollback()
        session.close()
