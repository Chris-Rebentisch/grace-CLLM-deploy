"""Integration tests for CQ database CRUD operations."""

from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.discovery.cq_database import (
    bulk_create_cqs,
    create_cluster,
    create_cq,
    get_cluster_members,
    get_cq_by_id,
    get_cq_summary,
    get_cqs_for_document,
    list_cqs,
    update_cq,
    update_cq_status,
    update_cq_verification,
)
from src.discovery.cq_models import (
    CQCluster,
    CQSource,
    CQStatus,
    CQType,
    CQVerificationStatus,
    CompetencyQuestion,
)
from src.shared.database import get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Original TRUNCATE pattern caused co-tenant
# interference in chunks 65–72b. Authorization: D485 / spec §6 Step 2.


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE competency_questions, cq_clusters CASCADE"))
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


def _make_cq(**overrides) -> CompetencyQuestion:
    """Create a CQ with sensible defaults."""
    defaults = {
        "canonical_text": "What types of insurance policies exist?",
        "source": CQSource.LLM_TOP_DOWN,
    }
    defaults.update(overrides)
    return CompetencyQuestion(**defaults)


def test_create_and_retrieve_cq(db_session):
    """Insert a CQ, retrieve by ID, verify fields match."""
    cq = _make_cq()
    created = create_cq(db_session, cq)
    assert created.id == cq.id

    retrieved = get_cq_by_id(db_session, cq.id)
    assert retrieved is not None
    assert retrieved.canonical_text == cq.canonical_text
    assert retrieved.source == CQSource.LLM_TOP_DOWN
    assert retrieved.status == CQStatus.DRAFT


def test_list_cqs_with_filters(db_session):
    """Insert CQs with different statuses/domains, verify filtering."""
    cq1 = _make_cq(canonical_text="CQ1", domain="legal", status=CQStatus.ACCEPTED)
    cq2 = _make_cq(canonical_text="CQ2", domain="insurance", status=CQStatus.DRAFT)
    cq3 = _make_cq(canonical_text="CQ3", domain="legal", status=CQStatus.DRAFT)
    for cq in [cq1, cq2, cq3]:
        create_cq(db_session, cq)

    # Filter by status
    accepted = list_cqs(db_session, status=CQStatus.ACCEPTED)
    assert len(accepted) == 1

    # Filter by domain
    legal = list_cqs(db_session, domain="legal")
    assert len(legal) == 2

    # All
    all_cqs = list_cqs(db_session)
    assert len(all_cqs) == 3


def test_update_cq_increments_version(db_session):
    """Update canonical_text, verify version increments and previous_text is stored."""
    cq = _make_cq(canonical_text="Original text")
    create_cq(db_session, cq)

    updated = update_cq(db_session, cq.id, {"canonical_text": "Revised text"})
    assert updated is not None
    assert updated.version == 2
    assert updated.previous_text == "Original text"
    assert updated.canonical_text == "Revised text"


def test_update_cq_status(db_session):
    """Update status from DRAFT to ACCEPTED."""
    cq = _make_cq()
    create_cq(db_session, cq)

    updated = update_cq_status(db_session, cq.id, CQStatus.ACCEPTED)
    assert updated is not None
    assert updated.status == CQStatus.ACCEPTED


def test_update_cq_verification(db_session):
    """Update verification fields (OE-Assist pattern)."""
    cq = _make_cq()
    create_cq(db_session, cq)

    updated = update_cq_verification(
        db_session, cq.id,
        verification_status=CQVerificationStatus.PASS,
        verification_confidence=0.95,
        verification_path="Company -> covers -> Insurance_Policy -> expiry_date",
    )
    assert updated is not None
    assert updated.verification_status == CQVerificationStatus.PASS
    assert updated.verification_confidence == 0.95
    assert "Insurance_Policy" in updated.verification_path


def test_bulk_create_cqs(db_session):
    """Bulk insert 10 CQs, verify all stored correctly."""
    cqs = [_make_cq(canonical_text=f"Bulk CQ {i}") for i in range(10)]
    created = bulk_create_cqs(db_session, cqs)
    assert len(created) == 10

    all_cqs = list_cqs(db_session)
    assert len(all_cqs) == 10


def test_get_cq_summary(db_session):
    """Insert mixed CQs, verify summary counts."""
    cqs = [
        _make_cq(canonical_text="A", status=CQStatus.DRAFT, domain="legal"),
        _make_cq(canonical_text="B", status=CQStatus.DRAFT, domain="insurance"),
        _make_cq(canonical_text="C", status=CQStatus.ACCEPTED, domain="legal"),
    ]
    for cq in cqs:
        create_cq(db_session, cq)

    summary = get_cq_summary(db_session)
    assert summary["total"] == 3
    assert summary["by_status"]["DRAFT"] == 2
    assert summary["by_status"]["ACCEPTED"] == 1
    assert summary["by_domain"]["legal"] == 2


def test_get_cqs_for_document(db_session):
    """Insert CQs with linked_document_ids, verify lookup."""
    doc_id = uuid4()
    cq1 = _make_cq(canonical_text="Linked CQ", linked_document_ids=[doc_id])
    cq2 = _make_cq(canonical_text="Unlinked CQ")
    create_cq(db_session, cq1)
    create_cq(db_session, cq2)

    linked = get_cqs_for_document(db_session, doc_id)
    assert len(linked) == 1
    assert linked[0].canonical_text == "Linked CQ"


def test_create_cluster_and_get_members(db_session):
    """Create cluster, assign CQs to it, retrieve members."""
    cluster = CQCluster(domain="insurance", member_count=2)
    created_cluster = create_cluster(db_session, cluster)

    cq1 = _make_cq(canonical_text="Cluster CQ 1", cluster_id=created_cluster.id)
    cq2 = _make_cq(canonical_text="Cluster CQ 2", cluster_id=created_cluster.id)
    create_cq(db_session, cq1)
    create_cq(db_session, cq2)

    members = get_cluster_members(db_session, created_cluster.id)
    assert len(members) == 2


def test_cq_cluster_relationship(db_session):
    """Verify FK between CQ and cluster works both directions."""
    cluster = CQCluster(domain="legal")
    created_cluster = create_cluster(db_session, cluster)

    cq = _make_cq(canonical_text="Test FK CQ", cluster_id=created_cluster.id)
    created_cq = create_cq(db_session, cq)

    # CQ points to cluster
    assert created_cq.cluster_id == created_cluster.id

    # Cluster can be updated to point to CQ
    from src.discovery.cq_database import update_cluster
    updated_cluster = update_cluster(
        db_session, created_cluster.id, {"canonical_cq_id": created_cq.id}
    )
    assert updated_cluster.canonical_cq_id == created_cq.id
