"""Unit tests for Guided Review Pydantic models and enums."""

from uuid import uuid4

import pytest

from src.ontology.review_models import (
    ChangeOfStatusEntityType,
    ChangeOfStatusEvent,
    ReviewDecision,
    ReviewDecisionType,
    ReviewElementStatus,
    ReviewElementType,
    ReviewSession,
    ReviewSessionStatus,
)


# --- ReviewSession Tests ---


def test_review_session_valid_construction():
    """ReviewSession with all required fields constructs successfully."""
    session = ReviewSession(
        reviewer="alice",
        seed_schema_merge_run_id="merge-run-001",
        seed_schema_snapshot={"entity_types": [], "relationships": []},
        total_entity_types=5,
        total_relationships=3,
    )
    assert session.reviewer == "alice"
    assert session.seed_schema_merge_run_id == "merge-run-001"
    assert session.total_entity_types == 5
    assert session.total_relationships == 3


def test_review_session_default_status():
    """ReviewSession default status is IN_PROGRESS."""
    session = ReviewSession(
        reviewer="bob",
        seed_schema_merge_run_id="merge-run-002",
        seed_schema_snapshot={},
    )
    assert session.status == ReviewSessionStatus.IN_PROGRESS


def test_review_session_resulting_version_id_accepts_none():
    """ReviewSession resulting_version_id defaults to None."""
    session = ReviewSession(
        reviewer="carol",
        seed_schema_merge_run_id="merge-run-003",
        seed_schema_snapshot={},
    )
    assert session.resulting_version_id is None

    # Also accepts an explicit UUID
    vid = uuid4()
    session2 = ReviewSession(
        reviewer="carol",
        seed_schema_merge_run_id="merge-run-003",
        seed_schema_snapshot={},
        resulting_version_id=vid,
    )
    assert session2.resulting_version_id == vid


# --- ReviewDecision Tests ---


def test_review_decision_valid_construction():
    """ReviewDecision with all required fields constructs successfully."""
    sid = uuid4()
    decision = ReviewDecision(
        session_id=sid,
        element_type=ReviewElementType.ENTITY_TYPE,
        element_name="Legal_Entity",
        decision=ReviewDecisionType.APPROVED,
        original_data={"name": "Legal_Entity", "properties": []},
        reviewer="alice",
    )
    assert decision.session_id == sid
    assert decision.element_name == "Legal_Entity"
    assert decision.decision == ReviewDecisionType.APPROVED


def test_review_decision_all_decision_types_valid():
    """All ReviewDecisionType values create valid decisions."""
    sid = uuid4()
    for dt in ReviewDecisionType:
        decision = ReviewDecision(
            session_id=sid,
            element_type=ReviewElementType.ENTITY_TYPE,
            element_name="TestType",
            decision=dt,
            original_data={"name": "TestType"},
            reviewer="tester",
        )
        assert decision.decision == dt


def test_review_decision_modified_data_accepts_none():
    """ReviewDecision modified_data defaults to None."""
    decision = ReviewDecision(
        session_id=uuid4(),
        element_type=ReviewElementType.RELATIONSHIP,
        element_name="has_member",
        decision=ReviewDecisionType.REJECTED,
        original_data={"name": "has_member"},
        reviewer="bob",
    )
    assert decision.modified_data is None


def test_review_decision_split_into_accepts_list():
    """ReviewDecision split_into accepts a list of dicts."""
    decision = ReviewDecision(
        session_id=uuid4(),
        element_type=ReviewElementType.ENTITY_TYPE,
        element_name="Organization",
        decision=ReviewDecisionType.SPLIT,
        original_data={"name": "Organization"},
        reviewer="carol",
        split_into=[
            {"name": "Company", "properties": []},
            {"name": "NonProfit", "properties": []},
        ],
    )
    assert len(decision.split_into) == 2
    assert decision.split_into[0]["name"] == "Company"


# --- ChangeOfStatusEvent Tests ---


def test_change_of_status_event_valid_construction():
    """ChangeOfStatusEvent with all required fields constructs successfully."""
    eid = uuid4()
    event = ChangeOfStatusEvent(
        entity_type=ChangeOfStatusEntityType.REVIEW_SESSION,
        entity_id=eid,
        from_status="none",
        to_status="in_progress",
        agent="alice",
    )
    assert event.entity_id == eid
    assert event.from_status == "none"
    assert event.to_status == "in_progress"


def test_change_of_status_all_entity_types():
    """All ChangeOfStatusEntityType values are valid."""
    eid = uuid4()
    for et in ChangeOfStatusEntityType:
        event = ChangeOfStatusEvent(
            entity_type=et,
            entity_id=eid,
            from_status="a",
            to_status="b",
            agent="system",
        )
        assert event.entity_type == et


# --- Enum Validation Tests ---


def test_all_enum_values():
    """All 5 enums have the expected number of values."""
    assert len(ReviewSessionStatus) == 3
    assert len(ReviewElementType) == 2
    assert len(ReviewDecisionType) == 9
    assert len(ReviewElementStatus) == 2
    assert len(ChangeOfStatusEntityType) == 4
