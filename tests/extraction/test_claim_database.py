"""Tests for extraction claims and events PostgreSQL CRUD operations."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.extraction.claim_database import (
    check_extraction_unit_exists,
    get_claim,
    get_extraction_event,
    insert_claim,
    insert_claims_batch,
    insert_extraction_event,
    list_claims,
    update_claim_status,
    update_claim_verdict,
    update_extraction_event_status,
)
from src.extraction.claim_models import (
    Claim,
    ClaimStatus,
    ClaimVerdict,
    ConstraintSeverity,
    ConstraintViolation,
    EvidenceSpan,
)


class TestClaimCRUD:
    """Tests for claims CRUD operations."""

    def test_insert_and_get_roundtrip(self, clean_extraction_tables, sample_claim):
        """insert_claim and get_claim round-trip correctly."""
        db = clean_extraction_tables
        claim_id = insert_claim(db, sample_claim)
        assert claim_id == sample_claim.claim_id

        retrieved = get_claim(db, claim_id)
        assert retrieved is not None
        assert retrieved.claim_id == sample_claim.claim_id
        assert retrieved.subject_name == "Acme Corp"
        assert retrieved.entity_type == "Legal_Entity"

    def test_insert_batch(self, clean_extraction_tables):
        """insert_claims_batch inserts multiple claims."""
        db = clean_extraction_tables
        claims = []
        for i in range(3):
            claims.append(
                Claim(
                    claim_id=str(uuid4()),
                    extraction_unit_id=f"unit-{i}",
                    subject_name=f"Entity {i}",
                    predicate="entity",
                    source_document_id="doc-001",
                    source_chunk_id=f"chunk-{i}",
                    created_at=datetime.now(UTC),
                )
            )
        count = insert_claims_batch(db, claims)
        assert count == 3

    def test_list_claims_no_filters(self, clean_extraction_tables, sample_claim):
        """list_claims with no filters returns all claims."""
        db = clean_extraction_tables
        insert_claim(db, sample_claim)
        results = list_claims(db)
        assert len(results) >= 1

    def test_list_claims_status_filter(self, clean_extraction_tables):
        """list_claims with status filter returns only matching claims."""
        db = clean_extraction_tables
        c1 = Claim(
            claim_id=str(uuid4()),
            extraction_unit_id="u1",
            subject_name="A",
            predicate="entity",
            status=ClaimStatus.AUTO_ACCEPTED,
            source_document_id="doc-001",
            source_chunk_id="chunk-001",
            created_at=datetime.now(UTC),
        )
        c2 = Claim(
            claim_id=str(uuid4()),
            extraction_unit_id="u2",
            subject_name="B",
            predicate="entity",
            status=ClaimStatus.QUARANTINED,
            source_document_id="doc-001",
            source_chunk_id="chunk-002",
            created_at=datetime.now(UTC),
        )
        insert_claim(db, c1)
        insert_claim(db, c2)

        accepted = list_claims(db, status=ClaimStatus.AUTO_ACCEPTED)
        assert all(c.status == ClaimStatus.AUTO_ACCEPTED for c in accepted)

        quarantined = list_claims(db, status=ClaimStatus.QUARANTINED)
        assert all(c.status == ClaimStatus.QUARANTINED for c in quarantined)

    def test_list_claims_verdict_filter(self, clean_extraction_tables, sample_claim):
        """list_claims with verdict filter returns only matching claims."""
        db = clean_extraction_tables
        insert_claim(db, sample_claim)
        # sample_claim has PENDING verdict but it's nullable in DB
        results = list_claims(db, verdict=ClaimVerdict.PENDING)
        assert all(c.verdict == ClaimVerdict.PENDING for c in results)

    def test_list_claims_pagination(self, clean_extraction_tables):
        """list_claims pagination works."""
        db = clean_extraction_tables
        for i in range(5):
            c = Claim(
                claim_id=str(uuid4()),
                extraction_unit_id=f"u-{i}",
                subject_name=f"Entity {i}",
                predicate="entity",
                source_document_id="doc-001",
                source_chunk_id=f"chunk-{i}",
                created_at=datetime.now(UTC),
            )
            insert_claim(db, c)

        page1 = list_claims(db, limit=2, offset=0)
        page2 = list_claims(db, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2

    def test_update_verdict(self, clean_extraction_tables, sample_claim):
        """update_claim_verdict changes verdict and confidence."""
        db = clean_extraction_tables
        insert_claim(db, sample_claim)
        updated = update_claim_verdict(
            db, sample_claim.claim_id, ClaimVerdict.SUPPORTED, 0.95
        )
        assert updated is True

        claim = get_claim(db, sample_claim.claim_id)
        assert claim.verdict == ClaimVerdict.SUPPORTED
        assert claim.confidence == pytest.approx(0.95)

    def test_update_status(self, clean_extraction_tables, sample_claim):
        """update_claim_status changes status and decision_source."""
        db = clean_extraction_tables
        insert_claim(db, sample_claim)
        updated = update_claim_status(
            db, sample_claim.claim_id, ClaimStatus.QUARANTINED, "verifier"
        )
        assert updated is True

        claim = get_claim(db, sample_claim.claim_id)
        assert claim.status == ClaimStatus.QUARANTINED
        assert claim.decision_source == "verifier"

    def test_check_extraction_unit_exists(self, clean_extraction_tables, sample_claim):
        """check_extraction_unit_exists returns True for existing, False for missing."""
        db = clean_extraction_tables
        insert_claim(db, sample_claim)

        assert check_extraction_unit_exists(db, sample_claim.extraction_unit_id) is True
        assert check_extraction_unit_exists(db, "nonexistent-unit") is False


class TestClaimRoundTripPatch:
    """Tests for Chunk 16 patch: new fields and JSONB round-trip."""

    def test_constraint_violations_roundtrip(self, clean_extraction_tables):
        """constraint_violations survive insert → get round-trip."""
        db = clean_extraction_tables
        claim = Claim(
            claim_id=str(uuid4()),
            extraction_unit_id="cv-unit-1",
            subject_name="TestEntity",
            predicate="entity",
            source_document_id="doc-cv",
            source_chunk_id="chunk-cv",
            constraint_violations=[
                ConstraintViolation(
                    severity=ConstraintSeverity.ERROR,
                    rule="invalid_entity_type",
                    message="Type 'Foo' not in ontology",
                ),
                ConstraintViolation(
                    severity=ConstraintSeverity.WARNING,
                    rule="domain_range_violation",
                    message="Range mismatch",
                ),
            ],
            created_at=datetime.now(UTC),
        )
        insert_claim(db, claim)
        retrieved = get_claim(db, claim.claim_id)
        assert retrieved is not None
        assert len(retrieved.constraint_violations) == 2
        assert retrieved.constraint_violations[0].severity == ConstraintSeverity.ERROR
        assert retrieved.constraint_violations[0].rule == "invalid_entity_type"
        assert retrieved.constraint_violations[1].severity == ConstraintSeverity.WARNING

    def test_evidence_spans_roundtrip(self, clean_extraction_tables):
        """evidence_spans survive insert → get round-trip."""
        db = clean_extraction_tables
        claim = Claim(
            claim_id=str(uuid4()),
            extraction_unit_id="es-unit-1",
            subject_name="TestEntity",
            predicate="entity",
            source_document_id="doc-es",
            source_chunk_id="chunk-es",
            evidence_spans=[
                EvidenceSpan(sentence_index=0, text="First sentence.", char_start=0, char_end=15),
                EvidenceSpan(sentence_index=1, text="Second sentence.", char_start=16, char_end=32),
            ],
            created_at=datetime.now(UTC),
        )
        insert_claim(db, claim)
        retrieved = get_claim(db, claim.claim_id)
        assert retrieved is not None
        assert len(retrieved.evidence_spans) == 2
        assert retrieved.evidence_spans[0].text == "First sentence."
        assert retrieved.evidence_spans[1].sentence_index == 1

    def test_extraction_event_id_roundtrip(self, clean_extraction_tables):
        """extraction_event_id survives insert → get round-trip."""
        db = clean_extraction_tables
        event_uuid = str(uuid4())
        claim = Claim(
            claim_id=str(uuid4()),
            extraction_unit_id="eid-unit-1",
            subject_name="TestEntity",
            predicate="entity",
            source_document_id="doc-eid",
            source_chunk_id="chunk-eid",
            extraction_event_id=event_uuid,
            created_at=datetime.now(UTC),
        )
        insert_claim(db, claim)
        retrieved = get_claim(db, claim.claim_id)
        assert retrieved is not None
        assert retrieved.extraction_event_id == event_uuid

    def test_verifier_model_roundtrip(self, clean_extraction_tables):
        """verifier_model survives insert → get round-trip."""
        db = clean_extraction_tables
        claim = Claim(
            claim_id=str(uuid4()),
            extraction_unit_id="vm-unit-1",
            subject_name="TestEntity",
            predicate="entity",
            source_document_id="doc-vm",
            source_chunk_id="chunk-vm",
            verifier_model="claude-haiku-4-5-20251001",
            created_at=datetime.now(UTC),
        )
        insert_claim(db, claim)
        retrieved = get_claim(db, claim.claim_id)
        assert retrieved is not None
        assert retrieved.verifier_model == "claude-haiku-4-5-20251001"


class TestExtractionEventsCRUD:
    """Tests for extraction events CRUD operations."""

    def test_insert_and_get_roundtrip(self, clean_extraction_tables, sample_extraction_event):
        """insert_extraction_event and get_extraction_event round-trip correctly."""
        db = clean_extraction_tables
        event_id = insert_extraction_event(db, sample_extraction_event)
        assert event_id == sample_extraction_event["event_id"]

        retrieved = get_extraction_event(db, event_id)
        assert retrieved is not None
        assert retrieved["source_document_id"] == "doc-001"
        assert retrieved["chunks_total"] == 5
        assert retrieved["status"] == "running"

    def test_update_event_status(self, clean_extraction_tables, sample_extraction_event):
        """update_extraction_event_status updates status and metrics."""
        db = clean_extraction_tables
        event_id = insert_extraction_event(db, sample_extraction_event)

        metrics = {
            "completed_at": datetime.now(UTC),
            "chunks_succeeded": 5,
            "chunks_failed": 0,
        }
        updated = update_extraction_event_status(db, event_id, "completed", metrics)
        assert updated is True

        event = get_extraction_event(db, event_id)
        assert event["status"] == "completed"
        assert event["chunks_succeeded"] == 5
