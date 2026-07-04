"""Tests for the Chunk 30 quarantined-claim API surface (D230).

Four tests, one per primary route behaviour:

1. List filtering + cursor pagination round-trip.
2. Accept happy path (status flip + graph write via per-claim writer).
3. Reject happy path (status flip; no graph write).
4. Edit-and-Accept supersession (original → SUPERSEDED, new claim promoted).

ArcadeDB is mocked at the per-claim-writer ``insert_entity`` boundary so
the suite can run without a live graph back-end. PostgreSQL is real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.extraction.claim_database import insert_claim
from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict
from src.graph.entity_models import EntityCreateResponse
from src.shared.database import get_engine


# --- Fixtures --------------------------------------------------------------


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_extraction_claims():
    """Wipe extraction_claims around every claim-route test to keep filters stable."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM extraction_claims"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM extraction_claims"))
        conn.commit()


def _seed_quarantined_claim(
    *,
    subject: str = "Acme Corp",
    ontology_module: str = "core",
    source_document_id: str = "doc-001",
    verdict: ClaimVerdict = ClaimVerdict.REFUTED,
) -> Claim:
    """Insert one quarantined claim into PostgreSQL and return the model."""
    engine = get_engine()
    from sqlalchemy.orm import Session

    claim = Claim(
        claim_id=str(uuid4()),
        extraction_unit_id=f"unit-{uuid4().hex[:12]}",
        entity_type="Legal_Entity",
        relationship_type=None,
        subject_name=subject,
        predicate="entity",
        object_name=None,
        properties_json={"jurisdiction": "Delaware"},
        verdict=verdict,
        status=ClaimStatus.QUARANTINED,
        decision_source="verifier",
        source_document_id=source_document_id,
        source_chunk_id="chunk-001",
        ontology_module=ontology_module,
        schema_version=1,
        extraction_event_id=str(uuid4()),
        created_at=datetime.now(UTC),
    )
    with Session(engine) as session:
        insert_claim(session, claim)
        session.commit()
    return claim


def _arcade_response(grace_id: str | None = None) -> EntityCreateResponse:
    return EntityCreateResponse(
        grace_id=grace_id or str(uuid4()),
        rid="#1:0",
        entity_type="Legal_Entity",
        created=True,
        canonical_match=False,
    )


def _claim_status(claim_id: str) -> str:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT status FROM extraction_claims WHERE claim_id = :cid"),
            {"cid": UUID(claim_id)},
        ).first()
    assert row is not None
    return row.status


# --- Tests -----------------------------------------------------------------


def test_list_claims_filters_and_cursor_pagination(client):
    """List filtering + cursor pagination round-trip with stable ordering."""
    # Seed 6 quarantined claims, alternating ontology_module so we can filter.
    for i in range(6):
        _seed_quarantined_claim(
            subject=f"Subject {i}",
            ontology_module="core" if i % 2 == 0 else "insurance",
        )

    # Page 1 with limit=2, filtered by ontology_module=core.
    resp = client.get(
        "/api/claims",
        params={"ontology_module": "core", "limit": 2, "status": "quarantined"},
        headers={"X-Graph-Scope": "all"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["next_cursor"] is not None
    assert all(item["ontology_module"] == "core" for item in body["items"])
    assert all(item["status"] == "quarantined" for item in body["items"])

    # Page 2 using the cursor.
    resp_page2 = client.get(
        "/api/claims",
        params={
            "ontology_module": "core",
            "limit": 2,
            "status": "quarantined",
            "cursor": body["next_cursor"],
        },
        headers={"X-Graph-Scope": "all"},
    )
    assert resp_page2.status_code == 200
    body2 = resp_page2.json()
    assert len(body2["items"]) == 1  # 3 core claims total → one left on page 2
    assert body2["next_cursor"] is None

    # Cross-page items are distinct.
    page1_ids = {it["claim_id"] for it in body["items"]}
    page2_ids = {it["claim_id"] for it in body2["items"]}
    assert page1_ids.isdisjoint(page2_ids)


def test_accept_claim_promotes_via_per_claim_writer(client):
    """POST /accept calls the per-claim writer; status flips to AUTO_ACCEPTED."""
    claim = _seed_quarantined_claim()

    fake_resp = _arcade_response()
    with patch(
        "src.extraction.claim_override_writer.insert_entity",
        new=AsyncMock(return_value=fake_resp),
    ) as patched:
        resp = client.post(
            f"/api/claims/{claim.claim_id}/accept",
            json={"reviewer": "alice", "notes": "looks good"},
            headers={"X-Graph-Scope": "all"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_id"] == claim.claim_id
    assert body["status"] == "auto_accepted"
    assert body["graph_write_result"]["entities_created"] == 1
    assert body["superseded_claim_id"] is None
    patched.assert_awaited_once()
    assert _claim_status(claim.claim_id) == "auto_accepted"


def test_reject_claim_flips_status_without_graph_write(client):
    """POST /reject flips to REJECTED with decision_source='human'; no graph call."""
    claim = _seed_quarantined_claim()

    with patch(
        "src.extraction.claim_override_writer.insert_entity",
        new=AsyncMock(),
    ) as patched_insert:
        resp = client.post(
            f"/api/claims/{claim.claim_id}/reject",
            json={"reviewer": "bob", "notes": "not in scope"},
            headers={"X-Graph-Scope": "all"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["claim_id"] == claim.claim_id
    assert body["status"] == "rejected"
    patched_insert.assert_not_awaited()
    assert _claim_status(claim.claim_id) == "rejected"

    # 404 path: unknown claim.
    missing_resp = client.post(
        f"/api/claims/{uuid4()}/reject",
        json={"reviewer": "bob"},
        headers={"X-Graph-Scope": "all"},
    )
    assert missing_resp.status_code == 404


def test_edit_and_accept_supersedes_original(client):
    """Edit-and-Accept writes a new claim with supersedes_claim_id; original → SUPERSEDED."""
    original = _seed_quarantined_claim(subject="Acme Corporation (typo)")

    fake_resp = _arcade_response()
    with patch(
        "src.extraction.claim_override_writer.insert_entity",
        new=AsyncMock(return_value=fake_resp),
    ):
        resp = client.post(
            f"/api/claims/{original.claim_id}/accept",
            json={
                "reviewer": "carol",
                "notes": "fixed the name",
                "modified_claim": {
                    "subject_name": "Acme Corporation",
                    "properties_json": {"jurisdiction": "Delaware", "type": "C-Corp"},
                },
            },
            headers={"X-Graph-Scope": "all"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["superseded_claim_id"] == original.claim_id
    new_claim_id = body["claim_id"]
    assert new_claim_id != original.claim_id
    assert body["status"] == "auto_accepted"

    # Original is now SUPERSEDED.
    assert _claim_status(original.claim_id) == "superseded"

    # New claim exists and points back at the original.
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT subject_name, supersedes_claim_id, status, decision_source "
                "FROM extraction_claims WHERE claim_id = :cid"
            ),
            {"cid": UUID(new_claim_id)},
        ).first()
    assert row is not None
    assert row.subject_name == "Acme Corporation"
    assert str(row.supersedes_claim_id) == original.claim_id
    assert row.status == ClaimStatus.AUTO_ACCEPTED.value
    assert row.decision_source == "human"
