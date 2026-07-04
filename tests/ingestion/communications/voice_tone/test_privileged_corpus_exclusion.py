"""F-35 regression: privileged-tagged communication_events must NOT enter the
voice corpus (features / greetings / exemplars / tone synthesis).

The validation run's exported voice card leaked "PRIVILEGED & CONFIDENTIAL —
ATTORNEY-CLIENT" excerpts because ``_fetch_sender_emails`` — the single corpus
source — did not filter privileged events. This test seeds one privileged and
one clean passed-to-extraction email for a sender and asserts the fetch returns
only the clean one. All writes happen inside a transaction that is rolled back,
so grace_test is left untouched.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text

from src.ingestion.communications.voice_tone.profile_generator import (
    _fetch_sender_emails,
)
from src.shared.database import get_session_factory


def test_privileged_events_excluded_from_voice_corpus():
    factory = get_session_factory()
    session = factory()
    try:
        source_id = uuid.uuid4()
        sender = f"corpus-test-{uuid.uuid4().hex[:8]}@example.com"

        session.execute(
            text(
                "INSERT INTO ingestion_sources "
                "(id, name, source_type, config_json, segment) "
                "VALUES (:id, :name, 'eml', '{}'::jsonb, 'test')"
            ),
            {"id": source_id, "name": f"src-{source_id}"},
        )

        def _insert_event(mid, body, tags):
            session.execute(
                text(
                    "INSERT INTO communication_events "
                    "(message_id, sender_email, recipients_json, source_id, "
                    " triage_tier_outcome, body_plain, sensitivity_tags) "
                    "VALUES (:mid, :sender, '[]'::jsonb, :sid, "
                    " 'passed_to_extraction', :body, :tags)"
                ),
                {
                    "mid": mid,
                    "sender": sender,
                    "sid": source_id,
                    "body": body,
                    "tags": tags,
                },
            )

        _insert_event("clean-1", "Regular scheduling note. See you Tuesday.", None)
        _insert_event(
            "priv-1",
            "PRIVILEGED & CONFIDENTIAL — ATTORNEY-CLIENT COMMUNICATION.",
            "|privileged|",
        )
        session.flush()

        emails = _fetch_sender_emails(session, sender)
        bodies = [e["body_plain"] for e in emails]

        assert any("scheduling note" in (b or "") for b in bodies), (
            "clean email should be in the corpus"
        )
        assert not any(
            "PRIVILEGED" in (b or "") for b in bodies
        ), "privileged-tagged email must be excluded from the voice corpus (F-35)"
    finally:
        session.rollback()
        session.close()
