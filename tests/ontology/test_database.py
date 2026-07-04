"""Integration tests for Ontology Management database CRUD operations."""

from uuid import uuid4

import pytest
from sqlalchemy import text

# D485 carve-out (Chunk 75a): this module genuinely requires empty-baseline
# semantics (count-from-zero, none-when-empty, append-only trigger enforcement).
# TRUNCATE retained with requires_db_wipe marker for D472 interlock.
pytestmark = pytest.mark.requires_db_wipe

from src.ontology.database import (
    OntologyVersionRow,
    SchemaProposalRow,
    CalibrationRecordRow,
    SchemaPromotionEventRow,
    create_calibration_record,
    create_promotion_event,
    create_proposal,
    create_version,
    get_active_version,
    get_latest_calibration,
    get_next_version_number,
    get_promotion_event_by_id,
    get_promotion_events_for_proposal,
    get_proposal_by_id,
    get_proposal_summary,
    get_version_by_id,
    get_version_by_number,
    list_calibration_history,
    list_proposals,
    list_versions,
    set_active_version,
    update_proposal_decision,
    update_proposal_status,
)
from src.ontology.evidence_bundle import EvidenceBundle
from src.ontology.models import (
    CalibrationRecord,
    HumanDecision,
    OntologyVersion,
    ProposalPriority,
    ProposalStatus,
    ProposalType,
    SchemaPromotionEvent,
    SchemaProposal,
    SignalType,
    VersionSource,
)
from src.shared.database import Base, get_db, get_engine


def _trigger_exists(conn, trigger_name: str, table_name: str) -> bool:
    """Check if a trigger exists on the given table."""
    result = conn.execute(text(
        "SELECT 1 FROM pg_trigger t JOIN pg_class c ON t.tgrelid = c.oid "
        "WHERE t.tgname = :trig AND c.relname = :tbl"
    ), {"trig": trigger_name, "tbl": table_name})
    return result.scalar() is not None


def _disable_triggers(conn):
    """Disable append-only / immutable triggers for test cleanup."""
    conn.execute(text("ALTER TABLE ontology_versions DISABLE TRIGGER trig_ontology_versions_immutable"))
    # c47a trigger may not exist if migration hasn't been applied.
    if _trigger_exists(conn, "trg_schema_proposals_append_only", "schema_proposals"):
        conn.execute(text("ALTER TABLE schema_proposals DISABLE TRIGGER trg_schema_proposals_append_only"))


def _enable_triggers(conn):
    """Re-enable triggers after cleanup."""
    if _trigger_exists(conn, "trg_schema_proposals_append_only", "schema_proposals"):
        conn.execute(text("ALTER TABLE schema_proposals ENABLE TRIGGER trg_schema_proposals_append_only"))
    conn.execute(text("ALTER TABLE ontology_versions ENABLE TRIGGER trig_ontology_versions_immutable"))


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean ontology tables before and after each test.

    Uses TRUNCATE ... CASCADE — bypasses row-level append-only triggers
    (FOR EACH ROW; harmless on TRUNCATE) and auto-includes FK referrers
    added by later chunks (governance_decision_events c50b,
    calibration_decisions c49a, etc.) so the fixture survives schema growth.
    """
    engine = get_engine()

    def _cleanup() -> None:
        with engine.connect() as conn:
            conn.execute(text(
                "TRUNCATE TABLE schema_promotion_events, calibration_records, "
                "schema_proposals, ontology_versions "
                "RESTART IDENTITY CASCADE"
            ))
            conn.commit()

    _cleanup()
    yield
    _cleanup()


@pytest.fixture()
def db_session():
    """Yield a database session for testing."""
    gen = get_db()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


# --- Helpers ---

def _make_version(**overrides) -> OntologyVersion:
    """Create an OntologyVersion with sensible defaults."""
    defaults = {
        "version_number": 1,
        "schema_json": {"type": "object", "properties": {}},
        "schema_modules": {"core": {}},
        "hash_chain": "abc123def456",
        "source": VersionSource.DISCOVERY,
        "is_active": False,
    }
    defaults.update(overrides)
    return OntologyVersion(**defaults)


def _make_proposal(version_id, **overrides) -> SchemaProposal:
    """Create a SchemaProposal with sensible defaults."""
    defaults = {
        "proposal_type": ProposalType.ADD_ENTITY_TYPE,
        "change_tier": 2,
        "kgcl_command": "create class LegalEntity",
        "proposed_diff": {"add": ["LegalEntity"]},
        "evidence": EvidenceBundle(
            source_signal_ids=[uuid4()],
            signal_type="A",
            signal_strength=0.75,
            affected_entity_types=["LegalEntity"],
            ontology_module="test",
        ),
        "signal_type": SignalType.SIGNAL_A,
        "raw_confidence": 0.85,
        "priority": ProposalPriority.MEDIUM,
        "current_schema_version_id": version_id,
        "ontology_module": "test",
        "dedup_hash": "abc123",
        "overflow": False,
    }
    defaults.update(overrides)
    return SchemaProposal(**defaults)


# --- OntologyVersion CRUD Tests ---


def test_create_and_retrieve_version(db_session):
    """Insert an OntologyVersion, retrieve by ID, verify fields match."""
    v = _make_version()
    created = create_version(db_session, v)
    assert created.id == v.id

    retrieved = get_version_by_id(db_session, v.id)
    assert retrieved is not None
    assert retrieved.version_number == 1
    assert retrieved.hash_chain == "abc123def456"
    assert retrieved.source == VersionSource.DISCOVERY


def test_get_version_by_id_nonexistent(db_session):
    """get_version_by_id returns None for nonexistent UUID."""
    result = get_version_by_id(db_session, uuid4())
    assert result is None


def test_get_version_by_number(db_session):
    """get_version_by_number returns the correct version."""
    v = _make_version(version_number=42)
    create_version(db_session, v)

    retrieved = get_version_by_number(db_session, 42)
    assert retrieved is not None
    assert retrieved.id == v.id


def test_get_active_version(db_session):
    """get_active_version returns the active version."""
    v1 = _make_version(version_number=1, is_active=False)
    v2 = _make_version(version_number=2, is_active=True)
    create_version(db_session, v1)
    create_version(db_session, v2)

    active = get_active_version(db_session)
    assert active is not None
    assert active.id == v2.id


def test_get_active_version_none_when_empty(db_session):
    """get_active_version returns None when no versions exist."""
    active = get_active_version(db_session)
    assert active is None


def test_set_active_version_swaps(db_session):
    """set_active_version swaps active flag: old→False, new→True."""
    v1 = _make_version(version_number=1, is_active=True)
    v2 = _make_version(version_number=2, is_active=False)
    create_version(db_session, v1)
    create_version(db_session, v2)

    result = set_active_version(db_session, v2.id)
    assert result is not None
    assert result.is_active is True

    # v1 should no longer be active
    v1_check = get_version_by_id(db_session, v1.id)
    assert v1_check.is_active is False


def test_set_active_version_nonexistent(db_session):
    """set_active_version returns None for nonexistent version_id."""
    result = set_active_version(db_session, uuid4())
    assert result is None


def test_list_versions_descending(db_session):
    """list_versions returns versions in descending order by version_number."""
    for i in [1, 2, 3]:
        create_version(db_session, _make_version(version_number=i))

    versions = list_versions(db_session)
    assert len(versions) == 3
    assert versions[0].version_number == 3
    assert versions[1].version_number == 2
    assert versions[2].version_number == 1


def test_list_versions_pagination(db_session):
    """list_versions supports limit/offset pagination."""
    for i in range(1, 6):
        create_version(db_session, _make_version(version_number=i))

    page = list_versions(db_session, limit=2, offset=1)
    assert len(page) == 2
    assert page[0].version_number == 4
    assert page[1].version_number == 3


def test_get_next_version_number_empty(db_session):
    """get_next_version_number returns 1 when no versions exist."""
    assert get_next_version_number(db_session) == 1


def test_get_next_version_number_existing(db_session):
    """get_next_version_number returns max+1 when versions exist."""
    create_version(db_session, _make_version(version_number=5))
    assert get_next_version_number(db_session) == 6


# --- SchemaProposal CRUD Tests ---


def test_create_and_retrieve_proposal(db_session):
    """Insert a SchemaProposal, retrieve by ID, verify fields match."""
    v = _make_version()
    create_version(db_session, v)

    p = _make_proposal(v.id)
    created = create_proposal(db_session, p)
    assert created.id == p.id

    retrieved = get_proposal_by_id(db_session, p.id)
    assert retrieved is not None
    assert retrieved.proposal_type == ProposalType.ADD_ENTITY_TYPE
    assert retrieved.raw_confidence == 0.85


def test_list_proposals_filter_by_status(db_session):
    """list_proposals filters by status correctly."""
    v = _make_version()
    create_version(db_session, v)

    p1 = _make_proposal(v.id, status=ProposalStatus.PENDING)
    p2 = _make_proposal(v.id, status=ProposalStatus.APPROVED)
    create_proposal(db_session, p1)
    create_proposal(db_session, p2)

    pending = list_proposals(db_session, status=ProposalStatus.PENDING)
    assert len(pending) == 1
    assert pending[0].status == ProposalStatus.PENDING


def test_list_proposals_filter_by_tier(db_session):
    """list_proposals filters by change_tier correctly."""
    v = _make_version()
    create_version(db_session, v)

    p1 = _make_proposal(v.id, change_tier=1, proposal_type=ProposalType.ADD_PROPERTY)
    p2 = _make_proposal(v.id, change_tier=3, proposal_type=ProposalType.SPLIT_TYPE)
    create_proposal(db_session, p1)
    create_proposal(db_session, p2)

    tier3 = list_proposals(db_session, change_tier=3)
    assert len(tier3) == 1
    assert tier3[0].change_tier == 3


def test_list_proposals_filter_by_signal_type(db_session):
    """list_proposals filters by signal_type correctly."""
    v = _make_version()
    create_version(db_session, v)

    p1 = _make_proposal(v.id, signal_type=SignalType.SIGNAL_A)
    p2 = _make_proposal(v.id, signal_type=SignalType.SIGNAL_B)
    create_proposal(db_session, p1)
    create_proposal(db_session, p2)

    signal_a = list_proposals(db_session, signal_type=SignalType.SIGNAL_A)
    assert len(signal_a) == 1
    assert signal_a[0].signal_type == SignalType.SIGNAL_A


def test_update_proposal_decision(db_session):
    """update_proposal_decision updates all decision fields."""
    v = _make_version()
    create_version(db_session, v)

    p = _make_proposal(v.id)
    create_proposal(db_session, p)

    updated = update_proposal_decision(
        db_session,
        p.id,
        human_decision=HumanDecision.MODIFIED,
        reviewer="alice",
        modification_distance=0.3,
        modified_diff={"updated": True},
    )
    assert updated is not None
    assert updated.human_decision == HumanDecision.MODIFIED
    assert updated.reviewer == "alice"
    assert updated.modification_distance == 0.3
    assert updated.modified_diff == {"updated": True}
    assert updated.status == ProposalStatus.MODIFIED
    assert updated.reviewed_at is not None


def test_update_proposal_status(db_session):
    """update_proposal_status updates status only."""
    v = _make_version()
    create_version(db_session, v)

    p = _make_proposal(v.id)
    create_proposal(db_session, p)

    updated = update_proposal_status(db_session, p.id, ProposalStatus.SUPERSEDED)
    assert updated is not None
    assert updated.status == ProposalStatus.SUPERSEDED


def test_get_proposal_summary(db_session):
    """get_proposal_summary returns correct counts."""
    v = _make_version()
    create_version(db_session, v)

    p1 = _make_proposal(v.id, status=ProposalStatus.PENDING, change_tier=1,
                         proposal_type=ProposalType.ADD_PROPERTY, signal_type=SignalType.SIGNAL_A)
    p2 = _make_proposal(v.id, status=ProposalStatus.PENDING, change_tier=2,
                         signal_type=SignalType.SIGNAL_A)
    p3 = _make_proposal(v.id, status=ProposalStatus.APPROVED, change_tier=2,
                         signal_type=SignalType.SIGNAL_B)
    for p in [p1, p2, p3]:
        create_proposal(db_session, p)

    summary = get_proposal_summary(db_session)
    assert summary["by_status"]["pending"] == 2
    assert summary["by_status"]["approved"] == 1
    assert summary["by_tier"][2] == 2
    assert summary["by_signal_type"]["signal_a"] == 2


# --- CalibrationRecord CRUD Tests ---


def test_create_and_retrieve_calibration(db_session):
    """Insert a CalibrationRecord, retrieve by tier, verify fields match."""
    r = CalibrationRecord(
        change_tier=1,
        confidence_band_low=0.7,
        confidence_band_high=0.9,
        approval_rate=0.95,
        sample_count=100,
        trust_score=0.92,
        autonomy_threshold=0.85,
    )
    created = create_calibration_record(db_session, r)
    assert created.id == r.id

    latest = get_latest_calibration(db_session, change_tier=1)
    assert latest is not None
    assert latest.approval_rate == 0.95


def test_get_latest_calibration(db_session):
    """get_latest_calibration returns the most recent for a tier."""
    import time
    r1 = CalibrationRecord(
        change_tier=2,
        confidence_band_low=0.5,
        confidence_band_high=0.7,
        approval_rate=0.80,
        sample_count=50,
        trust_score=0.75,
        autonomy_threshold=0.90,
    )
    create_calibration_record(db_session, r1)

    # Small delay to ensure different computed_at
    time.sleep(0.01)

    r2 = CalibrationRecord(
        change_tier=2,
        confidence_band_low=0.6,
        confidence_band_high=0.8,
        approval_rate=0.85,
        sample_count=60,
        trust_score=0.80,
        autonomy_threshold=0.88,
    )
    create_calibration_record(db_session, r2)

    latest = get_latest_calibration(db_session, change_tier=2)
    assert latest is not None
    assert latest.id == r2.id
    assert latest.approval_rate == 0.85


# --- SchemaPromotionEvent CRUD Tests ---


def test_create_and_retrieve_promotion_event(db_session):
    """Insert a SchemaPromotionEvent, retrieve by ID, verify fields match."""
    v = _make_version()
    create_version(db_session, v)

    p = _make_proposal(v.id)
    create_proposal(db_session, p)

    e = SchemaPromotionEvent(
        proposal_id=p.id,
        schema_version_before_id=v.id,
        proposed_schema_json={"type": "object"},
        gate_passed=True,
        cq_pass_rate=0.95,
        cq_total=20,
        cq_passing=19,
    )
    created = create_promotion_event(db_session, e)
    assert created.id == e.id

    retrieved = get_promotion_event_by_id(db_session, e.id)
    assert retrieved is not None
    assert retrieved.gate_passed is True
    assert retrieved.cq_pass_rate == 0.95


def test_get_promotion_events_for_proposal(db_session):
    """get_promotion_events_for_proposal returns correct events."""
    v = _make_version()
    create_version(db_session, v)

    p = _make_proposal(v.id)
    create_proposal(db_session, p)

    e1 = SchemaPromotionEvent(
        proposal_id=p.id,
        schema_version_before_id=v.id,
        proposed_schema_json={"type": "object"},
        gate_passed=False,
    )
    e2 = SchemaPromotionEvent(
        proposal_id=p.id,
        schema_version_before_id=v.id,
        proposed_schema_json={"type": "object", "v": 2},
        gate_passed=True,
    )
    create_promotion_event(db_session, e1)
    create_promotion_event(db_session, e2)

    events = get_promotion_events_for_proposal(db_session, p.id)
    assert len(events) == 2


# --- Append-Only Trigger Tests ---


def test_trigger_blocks_update_non_is_active(db_session):
    """Append-only trigger blocks UPDATE on non-is_active columns."""
    v = _make_version()
    create_version(db_session, v)

    # Try to update hash_chain via raw SQL (bypassing SQLAlchemy session cache)
    with pytest.raises(Exception, match="append-only"):
        db_session.execute(
            text("UPDATE ontology_versions SET hash_chain = 'tampered' WHERE id = :vid"),
            {"vid": str(v.id)},
        )
        db_session.commit()
    db_session.rollback()


def test_trigger_allows_update_is_active(db_session):
    """Append-only trigger allows UPDATE on is_active column."""
    v = _make_version(is_active=False)
    create_version(db_session, v)

    # Update is_active via raw SQL — should succeed
    db_session.execute(
        text("UPDATE ontology_versions SET is_active = TRUE WHERE id = :vid"),
        {"vid": str(v.id)},
    )
    db_session.commit()

    updated = get_version_by_id(db_session, v.id)
    assert updated.is_active is True


def test_trigger_blocks_delete(db_session):
    """Append-only trigger blocks DELETE."""
    v = _make_version()
    create_version(db_session, v)

    with pytest.raises(Exception, match="append-only"):
        db_session.execute(
            text("DELETE FROM ontology_versions WHERE id = :vid"),
            {"vid": str(v.id)},
        )
        db_session.commit()
    db_session.rollback()
