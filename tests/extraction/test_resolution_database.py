"""Tests for resolution database CRUD operations."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.extraction.resolution_database import (
    get_resolution_history,
    get_resolution_stats,
    insert_resolution_log,
    insert_resolution_logs_batch,
)


class _MockResult:
    """Minimal stand-in for EntityResolutionResult."""

    def __init__(self, **kwargs):
        self.extracted_name = kwargs.get("extracted_name", "Acme Corp")
        self.extracted_type = kwargs.get("extracted_type", "Legal_Entity")
        self.resolved_grace_id = kwargs.get("resolved_grace_id", None)
        self.matched_name = kwargs.get("matched_name", None)
        self.resolution_tier = kwargs.get("resolution_tier", "new")
        self.similarity_score = kwargs.get("similarity_score", None)
        self.blocking_key = kwargs.get("blocking_key", "type:Legal_Entity")
        self.candidate_count = kwargs.get("candidate_count", 0)
        self.candidates_json = kwargs.get("candidates_json", None)
        self.resolution_note = kwargs.get("resolution_note", None)


@pytest.fixture
def clean_resolution_tables(db_session):
    """Ensure resolution log is clean before test."""
    db_session.execute(text("DELETE FROM entity_resolution_log"))
    db_session.flush()
    return db_session


def test_insert_resolution_log_returns_id(clean_resolution_tables):
    """insert_resolution_log creates a row and returns a positive ID."""
    session = clean_resolution_tables
    result = _MockResult()
    row_id = insert_resolution_log(session, result)
    assert row_id > 0


def test_insert_resolution_logs_batch(clean_resolution_tables):
    """insert_resolution_logs_batch inserts multiple and returns count."""
    session = clean_resolution_tables
    results = [
        _MockResult(extracted_name="Acme Corp"),
        _MockResult(extracted_name="GlobalTech", resolution_tier="exact",
                    resolved_grace_id=str(uuid4()), matched_name="GlobalTech Solutions"),
    ]
    count = insert_resolution_logs_batch(session, results)
    assert count == 2


def test_batch_id_and_event_id_roundtrip(clean_resolution_tables):
    """batch_id and extraction_event_id round-trip on log rows."""
    session = clean_resolution_tables
    event_id = str(uuid4())
    batch_id = str(uuid4())
    result = _MockResult()
    insert_resolution_log(session, result, extraction_event_id=event_id, batch_id=batch_id)

    history = get_resolution_history(session, entity_name="Acme Corp")
    assert len(history) == 1
    assert history[0]["extraction_event_id"] == event_id
    assert history[0]["batch_id"] == batch_id


def test_get_resolution_stats_tier_counts(clean_resolution_tables):
    """get_resolution_stats returns correct tier counts."""
    session = clean_resolution_tables
    event_id = str(uuid4())
    results = [
        _MockResult(resolution_tier="exact", resolved_grace_id=str(uuid4()),
                    matched_name="Acme"),
        _MockResult(resolution_tier="exact", resolved_grace_id=str(uuid4()),
                    matched_name="Beta"),
        _MockResult(resolution_tier="new"),
        _MockResult(resolution_tier="embedding", similarity_score=0.92,
                    resolved_grace_id=str(uuid4()), matched_name="Gamma"),
    ]
    insert_resolution_logs_batch(session, results, extraction_event_id=event_id)

    stats = get_resolution_stats(session, extraction_event_id=event_id)
    assert stats["tier_counts"]["exact"] == 2
    assert stats["tier_counts"]["new"] == 1
    assert stats["tier_counts"]["embedding"] == 1
    assert stats["total"] == 4
    assert stats["new_count"] == 1
    assert stats["matched_count"] == 3


def test_get_resolution_stats_excludes_failures(clean_resolution_tables):
    """get_resolution_stats excludes llm_disambiguation_failed from calibration."""
    session = clean_resolution_tables
    results = [
        _MockResult(resolution_tier="new",
                    resolution_note="llm_disambiguation_failed"),
        _MockResult(resolution_tier="exact", resolved_grace_id=str(uuid4()),
                    matched_name="Acme"),
    ]
    insert_resolution_logs_batch(session, results)

    stats = get_resolution_stats(session)
    # Only the "exact" entry should be counted (note IS NULL filter)
    assert stats["total"] == 1
    assert stats["tier_counts"].get("new", 0) == 0


def test_get_resolution_history_filters(clean_resolution_tables):
    """get_resolution_history filters by name and type."""
    session = clean_resolution_tables
    results = [
        _MockResult(extracted_name="Acme Corp", extracted_type="Legal_Entity"),
        _MockResult(extracted_name="John Doe", extracted_type="Person"),
    ]
    insert_resolution_logs_batch(session, results)

    by_name = get_resolution_history(session, entity_name="Acme Corp")
    assert len(by_name) == 1
    assert by_name[0]["extracted_name"] == "Acme Corp"

    by_type = get_resolution_history(session, entity_type="Person")
    assert len(by_type) == 1
    assert by_type[0]["extracted_name"] == "John Doe"


def test_null_matched_grace_id_for_new(clean_resolution_tables):
    """Null matched_grace_id for new entities is stored correctly."""
    session = clean_resolution_tables
    result = _MockResult(resolution_tier="new", resolved_grace_id=None)
    insert_resolution_log(session, result)

    history = get_resolution_history(session, entity_name="Acme Corp")
    assert history[0]["matched_grace_id"] is None
    assert history[0]["resolution_tier"] == "new"
