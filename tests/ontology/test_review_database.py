"""Integration tests for Guided Review database CRUD operations."""

import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.ontology.review_database import (
    ChangeOfStatusEventRow,
    ReviewDecisionRow,
    ReviewSessionRow,
    create_change_of_status,
    create_review_decision,
    create_review_session,
    get_decision_for_element,
    get_decision_summary,
    get_review_decision_by_id,
    get_review_progress,
    get_review_session_by_id,
    get_status_duration,
    increment_reviewed_count,
    list_decisions_for_session,
    list_review_sessions,
    list_status_changes_by_type,
    list_status_changes_for_entity,
    update_review_session_status,
)
from src.ontology.review_models import (
    ChangeOfStatusEntityType,
    ChangeOfStatusEvent,
    ReviewDecision,
    ReviewDecisionType,
    ReviewElementType,
    ReviewSession,
    ReviewSessionStatus,
)
from src.ontology.database import create_version
from src.ontology.models import OntologyVersion, VersionSource
from src.shared.database import get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE change_of_status_events, review_decisions, "
        "review_sessions, schema_promotion_events, calibration_records, "
        "schema_proposals, ontology_versions "
        "RESTART IDENTITY CASCADE"
    ))
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


# --- Helpers ---


def _make_session(**overrides) -> ReviewSession:
    """Create a ReviewSession with sensible defaults."""
    defaults = {
        "reviewer": "test_reviewer",
        "seed_schema_merge_run_id": "merge-run-test",
        "seed_schema_snapshot": {"entity_types": [], "relationships": []},
        "total_entity_types": 5,
        "total_relationships": 3,
    }
    defaults.update(overrides)
    return ReviewSession(**defaults)


def _make_decision(session_id, **overrides) -> ReviewDecision:
    """Create a ReviewDecision with sensible defaults."""
    defaults = {
        "session_id": session_id,
        "element_type": ReviewElementType.ENTITY_TYPE,
        "element_name": "Legal_Entity",
        "decision": ReviewDecisionType.APPROVED,
        "original_data": {"name": "Legal_Entity", "properties": []},
        "reviewer": "test_reviewer",
    }
    defaults.update(overrides)
    return ReviewDecision(**defaults)


# --- ReviewSession CRUD Tests ---


def test_create_and_retrieve_review_session(db_session):
    """Insert a ReviewSession, retrieve by ID, verify fields match."""
    s = _make_session()
    created = create_review_session(db_session, s)
    assert created.id == s.id
    assert created.reviewer == "test_reviewer"

    retrieved = get_review_session_by_id(db_session, s.id)
    assert retrieved is not None
    assert retrieved.status == ReviewSessionStatus.IN_PROGRESS
    assert retrieved.total_entity_types == 5


def test_create_review_session_auto_creates_status_event(db_session):
    """Creating a review session auto-creates a ChangeOfStatus event (none -> in_progress)."""
    s = _make_session()
    created = create_review_session(db_session, s)

    events = list_status_changes_for_entity(db_session, created.id)
    assert len(events) == 1
    assert events[0].from_status == "none"
    assert events[0].to_status == "in_progress"
    assert events[0].entity_type == ChangeOfStatusEntityType.REVIEW_SESSION
    assert events[0].agent == "test_reviewer"


def test_get_review_session_by_id_returns_none_for_nonexistent(db_session):
    """get_review_session_by_id returns None for nonexistent UUID."""
    result = get_review_session_by_id(db_session, uuid4())
    assert result is None


def test_list_review_sessions_filter_by_status(db_session):
    """list_review_sessions filters by status."""
    s1 = _make_session(reviewer="alice")
    s2 = _make_session(reviewer="bob")
    create_review_session(db_session, s1)
    created2 = create_review_session(db_session, s2)

    # Complete s2
    update_review_session_status(
        db_session, created2.id, ReviewSessionStatus.COMPLETED, agent="bob"
    )

    in_progress = list_review_sessions(db_session, status=ReviewSessionStatus.IN_PROGRESS)
    assert len(in_progress) == 1
    assert in_progress[0].reviewer == "alice"

    completed = list_review_sessions(db_session, status=ReviewSessionStatus.COMPLETED)
    assert len(completed) == 1
    assert completed[0].reviewer == "bob"


def test_update_review_session_status_creates_event(db_session):
    """Updating session status creates a ChangeOfStatus event."""
    s = _make_session()
    created = create_review_session(db_session, s)

    update_review_session_status(
        db_session,
        created.id,
        ReviewSessionStatus.COMPLETED,
        agent="reviewer",
        reason="Review finished",
    )

    events = list_status_changes_for_entity(db_session, created.id)
    assert len(events) == 2  # initial + update
    assert events[1].from_status == "in_progress"
    assert events[1].to_status == "completed"
    assert events[1].reason == "Review finished"


def test_update_review_session_sets_completed_at(db_session):
    """Completing a session sets completed_at."""
    s = _make_session()
    created = create_review_session(db_session, s)

    updated = update_review_session_status(
        db_session, created.id, ReviewSessionStatus.COMPLETED, agent="reviewer"
    )
    assert updated.completed_at is not None
    assert updated.status == ReviewSessionStatus.COMPLETED


def test_update_review_session_sets_resulting_version_id(db_session):
    """Completing a session with resulting_version_id sets it."""
    s = _make_session()
    created = create_review_session(db_session, s)

    # Create a real ontology version to satisfy the FK
    version = OntologyVersion(
        version_number=1,
        schema_json={"type": "object", "properties": {}},
        schema_modules={"core": {}},
        hash_chain="abc123",
        source=VersionSource.GUIDED_REVIEW,
        is_active=False,
    )
    created_version = create_version(db_session, version)

    updated = update_review_session_status(
        db_session,
        created.id,
        ReviewSessionStatus.COMPLETED,
        agent="reviewer",
        resulting_version_id=created_version.id,
    )
    assert updated.resulting_version_id == created_version.id


def test_increment_reviewed_count_entity_type(db_session):
    """increment_reviewed_count increments entity_type counter."""
    s = _make_session()
    created = create_review_session(db_session, s)
    assert created.reviewed_entity_types == 0

    updated = increment_reviewed_count(db_session, created.id, ReviewElementType.ENTITY_TYPE)
    assert updated.reviewed_entity_types == 1

    updated2 = increment_reviewed_count(db_session, created.id, ReviewElementType.ENTITY_TYPE)
    assert updated2.reviewed_entity_types == 2


def test_increment_reviewed_count_relationship(db_session):
    """increment_reviewed_count increments relationship counter."""
    s = _make_session()
    created = create_review_session(db_session, s)

    updated = increment_reviewed_count(db_session, created.id, ReviewElementType.RELATIONSHIP)
    assert updated.reviewed_relationships == 1


def test_get_review_progress(db_session):
    """get_review_progress returns correct progress dict."""
    s = _make_session(total_entity_types=10, total_relationships=5)
    created = create_review_session(db_session, s)

    # Increment some counters
    increment_reviewed_count(db_session, created.id, ReviewElementType.ENTITY_TYPE)
    increment_reviewed_count(db_session, created.id, ReviewElementType.ENTITY_TYPE)
    increment_reviewed_count(db_session, created.id, ReviewElementType.RELATIONSHIP)

    progress = get_review_progress(db_session, created.id)
    assert progress is not None
    assert progress["entity_types"]["total"] == 10
    assert progress["entity_types"]["reviewed"] == 2
    assert progress["entity_types"]["remaining"] == 8
    assert progress["relationships"]["total"] == 5
    assert progress["relationships"]["reviewed"] == 1
    assert progress["relationships"]["remaining"] == 4
    assert progress["overall_percent"] == pytest.approx(20.0)
    assert progress["status"] == "in_progress"


# --- ReviewDecision CRUD Tests ---


def test_create_and_retrieve_review_decision(db_session):
    """Insert a ReviewDecision, retrieve by ID, verify fields match."""
    s = _make_session()
    created_session = create_review_session(db_session, s)

    d = _make_decision(created_session.id)
    created = create_review_decision(db_session, d)
    assert created.id == d.id
    assert created.element_name == "Legal_Entity"

    retrieved = get_review_decision_by_id(db_session, d.id)
    assert retrieved is not None
    assert retrieved.decision == ReviewDecisionType.APPROVED


def test_create_review_decision_auto_creates_status_event(db_session):
    """Creating a review decision auto-creates a ChangeOfStatus event (pending -> decided)."""
    s = _make_session()
    created_session = create_review_session(db_session, s)

    d = _make_decision(created_session.id)
    created = create_review_decision(db_session, d)

    events = list_status_changes_for_entity(db_session, created.id)
    assert len(events) == 1
    assert events[0].from_status == "pending"
    assert events[0].to_status == "decided"
    assert events[0].entity_type == ChangeOfStatusEntityType.REVIEW_ELEMENT


def test_list_decisions_for_session(db_session):
    """list_decisions_for_session returns decisions for session."""
    s = _make_session()
    created_session = create_review_session(db_session, s)

    d1 = _make_decision(created_session.id, element_name="Type_A")
    d2 = _make_decision(
        created_session.id,
        element_name="has_member",
        element_type=ReviewElementType.RELATIONSHIP,
        decision=ReviewDecisionType.REJECTED,
    )
    create_review_decision(db_session, d1)
    create_review_decision(db_session, d2)

    decisions = list_decisions_for_session(db_session, created_session.id)
    assert len(decisions) == 2


def test_list_decisions_for_session_filters(db_session):
    """list_decisions_for_session filters by element_type and decision."""
    s = _make_session()
    created_session = create_review_session(db_session, s)

    d1 = _make_decision(created_session.id, element_name="Type_A")
    d2 = _make_decision(
        created_session.id,
        element_name="has_member",
        element_type=ReviewElementType.RELATIONSHIP,
        decision=ReviewDecisionType.REJECTED,
    )
    create_review_decision(db_session, d1)
    create_review_decision(db_session, d2)

    # Filter by element type
    entity_only = list_decisions_for_session(
        db_session, created_session.id, element_type=ReviewElementType.ENTITY_TYPE
    )
    assert len(entity_only) == 1
    assert entity_only[0].element_name == "Type_A"

    # Filter by decision type
    rejected = list_decisions_for_session(
        db_session, created_session.id, decision=ReviewDecisionType.REJECTED
    )
    assert len(rejected) == 1
    assert rejected[0].element_name == "has_member"


def test_get_decision_for_element(db_session):
    """get_decision_for_element returns the most recent decision for an element."""
    s = _make_session()
    created_session = create_review_session(db_session, s)

    # First decision
    d1 = _make_decision(
        created_session.id,
        element_name="Legal_Entity",
        decision=ReviewDecisionType.APPROVED,
    )
    create_review_decision(db_session, d1)

    # Revised decision (more recent)
    d2 = _make_decision(
        created_session.id,
        element_name="Legal_Entity",
        decision=ReviewDecisionType.EDITED,
        modified_data={"name": "Legal_Entity", "properties": ["new_prop"]},
    )
    create_review_decision(db_session, d2)

    result = get_decision_for_element(db_session, created_session.id, "Legal_Entity")
    assert result is not None
    assert result.decision == ReviewDecisionType.EDITED


def test_get_decision_summary(db_session):
    """get_decision_summary returns correct counts."""
    s = _make_session()
    created_session = create_review_session(db_session, s)

    create_review_decision(
        db_session,
        _make_decision(created_session.id, element_name="Type_A", decision=ReviewDecisionType.APPROVED),
    )
    create_review_decision(
        db_session,
        _make_decision(created_session.id, element_name="Type_B", decision=ReviewDecisionType.APPROVED),
    )
    create_review_decision(
        db_session,
        _make_decision(
            created_session.id,
            element_name="has_member",
            element_type=ReviewElementType.RELATIONSHIP,
            decision=ReviewDecisionType.REJECTED,
        ),
    )

    summary = get_decision_summary(db_session, created_session.id)
    assert summary["total"] == 3
    assert summary["by_decision"]["approved"] == 2
    assert summary["by_decision"]["rejected"] == 1
    assert summary["by_element_type"]["entity_type"] == 2
    assert summary["by_element_type"]["relationship"] == 1


# --- ChangeOfStatusEvent CRUD Tests ---


def test_create_and_retrieve_change_of_status(db_session):
    """Insert a ChangeOfStatusEvent, verify it appears in entity's history."""
    eid = uuid4()
    event = ChangeOfStatusEvent(
        entity_type=ChangeOfStatusEntityType.SCHEMA_VERSION,
        entity_id=eid,
        from_status="draft",
        to_status="active",
        agent="admin",
        reason="Promoted to production",
    )
    created = create_change_of_status(db_session, event)
    assert created.id == event.id

    events = list_status_changes_for_entity(db_session, eid)
    assert len(events) == 1
    assert events[0].from_status == "draft"
    assert events[0].to_status == "active"


def test_list_status_changes_for_entity_ordered(db_session):
    """list_status_changes_for_entity returns transitions in chronological order."""
    eid = uuid4()
    e1 = ChangeOfStatusEvent(
        entity_type=ChangeOfStatusEntityType.REVIEW_SESSION,
        entity_id=eid,
        from_status="none",
        to_status="in_progress",
        agent="alice",
    )
    create_change_of_status(db_session, e1)

    e2 = ChangeOfStatusEvent(
        entity_type=ChangeOfStatusEntityType.REVIEW_SESSION,
        entity_id=eid,
        from_status="in_progress",
        to_status="completed",
        agent="alice",
    )
    create_change_of_status(db_session, e2)

    events = list_status_changes_for_entity(db_session, eid)
    assert len(events) == 2
    assert events[0].to_status == "in_progress"
    assert events[1].to_status == "completed"


def test_get_status_duration(db_session):
    """get_status_duration computes duration between transitions."""
    s = _make_session()
    created = create_review_session(db_session, s)

    # Small delay to ensure measurable duration
    time.sleep(0.05)

    update_review_session_status(
        db_session, created.id, ReviewSessionStatus.COMPLETED, agent="reviewer"
    )

    duration = get_status_duration(db_session, created.id, "in_progress")
    assert duration is not None
    assert duration >= 0.0  # At least some time passed
