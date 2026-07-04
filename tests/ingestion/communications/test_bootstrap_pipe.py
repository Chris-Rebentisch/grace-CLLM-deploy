"""CP5 — Bootstrap pipe tests (D518).

Tests bootstrap roundtrip, list_documents integration, and sentinel_status flip.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.ingestion.communications.bootstrap_pipe import run_bootstrap
from src.ingestion.models import CuratedEmailSubsetRow
from src.shared.database import get_session_factory


@pytest.fixture()
def db_session():
    """Provide a test database session."""
    factory = get_session_factory()
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def setup_bootstrap_data(db_session):
    """Insert test data: ingestion_sources, communication_events, curated_email_subsets.

    Returns (subset_id, source_id, msg_ids).
    Cleans up all inserted rows after the test.
    """
    source_id = uuid4()
    subset_id = uuid4()
    unique_suffix = uuid4().hex[:8]
    msg_ids = [f"test-msg-{uuid4().hex[:8]}@example.com" for _ in range(2)]
    event_ids = []

    # Create parent ingestion_sources row (FK target)
    db_session.execute(
        text(
            "INSERT INTO ingestion_sources (id, name, source_type, config_json, segment, status) "
            "VALUES (:id, :name, :st, :cj, :seg, :status)"
        ),
        {
            "id": str(source_id),
            "name": f"test-bootstrap-{unique_suffix}",
            "st": "imap",
            "cj": "{}",
            "seg": "test",
            "status": "ready",
        },
    )
    db_session.commit()

    # Create communication_events rows
    for msg_id in msg_ids:
        eid = uuid4()
        event_ids.append(eid)
        db_session.execute(
            text(
                "INSERT INTO communication_events "
                "(id, source_id, message_id, sender_email, sender_display_name, "
                "subject, sent_at, body_plain, triage_tier_outcome, recipients_json) "
                "VALUES (:id, :sid, :mid, :se, :sdn, :sub, :sa, :bp, :tto, :rj)"
            ),
            {
                "id": str(eid),
                "sid": str(source_id),
                "mid": msg_id,
                "se": "sender@example.com",
                "sdn": "Test Sender",
                "sub": "Test Subject",
                "sa": datetime.now(UTC),
                "bp": "This is a test email body about Acme Corp.",
                "tto": "passed_to_extraction",
                "rj": "[]",
            },
        )
    db_session.commit()

    # Create curated_email_subsets row
    subset = CuratedEmailSubsetRow(
        id=subset_id,
        source_id=source_id,
        deployment_path="B",
        selected_message_ids=msg_ids,
        diversity_metrics={"count": len(msg_ids)},
        created_by="test",
        sentinel_status="ready",
    )
    db_session.add(subset)
    db_session.commit()

    yield subset_id, source_id, msg_ids

    # Cleanup — curated_email_subsets is append-only (trigger blocks DELETE),
    # so we clean up only processed_documents (our output) and leave the
    # test scaffolding rows. All IDs are unique per test invocation.
    for msg_id in msg_ids:
        synthetic_path = f"email://{source_id}/{msg_id}"
        db_session.execute(
            text("DELETE FROM processed_documents WHERE file_path = :fp"),
            {"fp": synthetic_path},
        )
    db_session.commit()


def test_bootstrap_roundtrip(db_session, setup_bootstrap_data):
    """Bootstrap -> processed_documents rows created with correct origin and synthetic file_path."""
    subset_id, source_id, msg_ids = setup_bootstrap_data

    count = run_bootstrap(subset_id)
    assert count == len(msg_ids)

    # Verify rows in processed_documents
    for msg_id in msg_ids:
        synthetic_path = f"email://{source_id}/{msg_id}"
        row = db_session.execute(
            text("SELECT origin, source_type, status, file_path FROM processed_documents WHERE file_path = :fp"),
            {"fp": synthetic_path},
        ).first()
        assert row is not None, f"No processed_documents row for {synthetic_path}"
        assert row.origin == "curated_email"
        assert row.source_type == "curated_email"
        assert row.status == "COMPLETE"
        assert row.file_path.startswith("email://")


def test_list_documents_returns_email_rows(db_session, setup_bootstrap_data):
    """After bootstrap, list_documents returns email-origin rows without Discovery code change."""
    subset_id, source_id, msg_ids = setup_bootstrap_data

    count = run_bootstrap(subset_id)
    assert count > 0

    from src.discovery.database import list_documents
    from src.discovery.models import ProcessingStatus

    docs = list_documents(db_session, status=ProcessingStatus.COMPLETE)
    email_docs = [d for d in docs if d.origin == "curated_email"]
    assert len(email_docs) >= len(msg_ids)


def test_sentinel_status_flip(db_session, setup_bootstrap_data):
    """After bootstrap, curated_email_subsets.sentinel_status is 'consumed'."""
    subset_id, source_id, msg_ids = setup_bootstrap_data

    count = run_bootstrap(subset_id)
    assert count > 0

    # Refresh subset row
    db_session.expire_all()
    subset = db_session.query(CuratedEmailSubsetRow).filter_by(id=subset_id).first()
    assert subset is not None
    assert subset.sentinel_status == "consumed"
