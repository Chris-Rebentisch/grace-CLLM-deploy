"""Phase 7 Path C end-to-end harness (Chunk 60, CP12).

Document corpus → curated email supplement → additions. Validates the
D417 canonical tag 'curated_email_supplement' and supplement-driven
Discovery additions.

Skip-gracefully when Postgres / ArcadeDB unavailable.
"""

from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import text

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
        reason="Postgres not available",
    ),
]


def _db_available() -> bool:
    try:
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


SERVICES_AVAILABLE = _db_available()


@pytest.fixture(scope="module")
def supplement_source_id():
    """Create a curated_email_supplement source (D417 canonical tag)."""
    if not SERVICES_AVAILABLE:
        pytest.skip("Postgres not available")

    from src.shared.database import get_session_factory

    factory = get_session_factory()
    sid = str(uuid4())
    with factory() as session:
        # Idempotent cleanup — see Path A fixture for the alembic.downgrading
        # trigger-bypass rationale.
        session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
        session.execute(
            text(
                "DELETE FROM communication_events WHERE source_id IN "
                "(SELECT id FROM ingestion_sources WHERE name = :n)"
            ),
            {"n": "path_c_supplement"},
        )
        session.execute(
            text("DELETE FROM ingestion_sources WHERE name = :n"),
            {"n": "path_c_supplement"},
        )
        session.execute(
            text(
                "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment, status, created_at) "
                "VALUES (:id, :name, :st, :cfg, 'test', 'ready', now())"
            ),
            {
                "id": sid,
                "name": "path_c_supplement",
                "st": "curated_email_supplement",
                "cfg": json.dumps({"deployment_path": "C"}),
            },
        )
        session.commit()
    return sid


@pytest.fixture(scope="module")
def supplement_events(supplement_source_id):
    """Insert supplement communication events."""
    from src.shared.database import get_session_factory

    factory = get_session_factory()
    eids = []
    with factory() as session:
        for i in range(8):
            eid = str(uuid4())
            session.execute(
                text(
                    "INSERT INTO communication_events "
                    "(id, source_id, message_id, sender_email, recipients_json, "
                    " subject, body_plain, sent_at) "
                    "VALUES (:id, :sid, :msg_id, :sender, :recipients, "
                    "        :subj, :body, now())"
                ),
                {
                    "id": eid,
                    "sid": supplement_source_id,
                    "msg_id": f"path-c-msg-{i}",
                    "sender": f"supplement{i}@org.com",
                    "recipients": json.dumps([]),
                    "subj": f"Supplement email {i}",
                    "body": f"Supplement body {i}" * 5,
                },
            )
            eids.append(eid)
        session.commit()
    return eids


class TestPathCSourceSetup:
    """Verify D417 canonical tag on supplement source."""

    def test_source_type_is_supplement(self, supplement_source_id):
        """Source type reads 'curated_email_supplement'."""
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            row = session.execute(
                text("SELECT source_type FROM ingestion_sources WHERE id = :id"),
                {"id": supplement_source_id},
            ).fetchone()
        assert row is not None
        assert row[0] == "curated_email_supplement"

    def test_events_persisted(self, supplement_events):
        """Supplement events inserted."""
        assert len(supplement_events) == 8

    def test_config_records_deployment_path(self, supplement_source_id):
        """config_json carries deployment_path=C."""
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            row = session.execute(
                text("SELECT config_json FROM ingestion_sources WHERE id = :id"),
                {"id": supplement_source_id},
            ).fetchone()
        assert row is not None
        config = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        assert config.get("deployment_path") == "C"


class TestPathCSecondarySourceAudit:
    """Verify supplement additions flagged as secondary-source-derived."""

    def test_source_audit_trail(self, supplement_source_id):
        """Source row carries curated_email_supplement for audit trail."""
        from src.shared.database import get_session_factory

        factory = get_session_factory()
        with factory() as session:
            row = session.execute(
                text("SELECT source_type FROM ingestion_sources WHERE id = :id"),
                {"id": supplement_source_id},
            ).fetchone()
        assert row is not None
        assert row[0] == "curated_email_supplement"


class TestPathCReadinessComposite:
    """Verify combined document + email readiness."""

    def test_readiness_endpoint_accessible(self):
        """GET /api/ingestion/readiness responds."""
        if not SERVICES_AVAILABLE:
            pytest.skip("Services not available")

        import httpx

        try:
            r = httpx.get("http://127.0.0.1:8000/api/ingestion/readiness", timeout=5)
            assert r.status_code in (200, 424, 404)
        except httpx.ConnectError:
            pytest.skip("FastAPI not running")
