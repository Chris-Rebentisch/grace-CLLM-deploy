"""F-014 / ISS-0012 — server-side audit emission for REST-driven human gates.

Driving the documented REST APIs directly (review decide/complete, ontology
ratify, claims accept/reject) previously left ``elicitation_events`` empty —
event capture was entirely client-side. These tests prove each route now
enqueues an elicitation event server-side (bridge mocked; enqueue asserted
with the request's ``reviewer`` as agent context) and that an audit-event
failure is log-and-continue (never breaks the route).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session as SASession

from src.extraction.claim_database import insert_claim
from src.extraction.claim_models import Claim, ClaimStatus, ClaimVerdict
from src.graph.entity_models import EntityCreateResponse
from src.shared.database import get_db, get_engine

from tests.api.test_review_routes import SAMPLE_SEED_SCHEMA


# --- Fixtures (D485 SAVEPOINT-rollback pattern, mirrors test_review_routes) ---


@pytest.fixture()
def _db_rollback():
    """SAVEPOINT-rollback isolation for API tests (D485).

    NOT autouse: the claim tests below seed/clean ``extraction_claims`` on a
    separate real connection (mirroring test_claim_routes.py); running them
    under this overridden SAVEPOINT session would leave row locks held by the
    open outer transaction and deadlock the cleanup DELETE. Review/ratify
    tests request this fixture explicitly.
    """
    from src.api.main import app

    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text(
        "TRUNCATE TABLE change_of_status_events, review_decisions, "
        "review_sessions, schema_promotion_events, calibration_records, "
        "schema_proposals, ontology_versions "
        "RESTART IDENTITY CASCADE"
    ))
    session = SASession(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    def override_get_db():
        try:
            yield session
        finally:
            pass  # Don't close — outer fixture handles cleanup

    app.dependency_overrides[get_db] = override_get_db
    yield

    app.dependency_overrides.pop(get_db, None)
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from src.api.main import app
    return TestClient(app)


def _start_session(client) -> dict:
    resp = client.post("/api/ontology/review/start", json={
        "merge_run_id": "f014-merge-run",
        "reviewer": "reviewer-f014",
        "seed_schema_data": SAMPLE_SEED_SCHEMA,
    })
    assert resp.status_code == 200
    return resp.json()


# --- Review decide -----------------------------------------------------------


def test_review_decide_emits_server_side_event(_db_rollback, client):
    created = _start_session(client)
    with patch("src.api.review_routes.enqueue_event") as enqueue_mock:
        resp = client.post(f"/api/ontology/review/{created['id']}/decide", json={
            "element_type": "entity_type",
            "element_name": "Company",
            "decision": "approved",
            "reviewer": "reviewer-f014",
            "notes": "looks right",
        })
    assert resp.status_code == 200
    enqueue_mock.assert_called_once()
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["event_type"] == "mcp_review_decided"
    assert kwargs["agent_id"] == "reviewer-f014"
    assert kwargs["payload"]["element_name"] == "Company"
    assert kwargs["payload"]["decision"] == "approved"
    assert kwargs["payload"]["agent_id"] == "reviewer-f014"
    assert str(kwargs["session_id_override"]) == created["id"]


def test_review_decide_survives_audit_failure(_db_rollback, client):
    """Log-and-continue: audit-event failure must never break the route."""
    created = _start_session(client)
    with patch(
        "src.api.review_routes.enqueue_event",
        side_effect=RuntimeError("audit backend down"),
    ):
        resp = client.post(f"/api/ontology/review/{created['id']}/decide", json={
            "element_type": "entity_type",
            "element_name": "Company",
            "decision": "approved",
            "reviewer": "reviewer-f014",
        })
    assert resp.status_code == 200


# --- Review complete ---------------------------------------------------------


def test_review_complete_emits_server_side_event(_db_rollback, client):
    created = _start_session(client)
    with patch("src.api.review_routes.enqueue_event") as enqueue_mock:
        resp = client.post(f"/api/ontology/review/{created['id']}/complete", json={
            "reviewer": "reviewer-f014",
            "force": True,
        })
    assert resp.status_code == 200
    enqueue_mock.assert_called_once()
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["event_type"] == "mcp_session_closed"
    assert kwargs["agent_id"] == "reviewer-f014"
    assert kwargs["payload"]["session_id"] == created["id"]
    assert kwargs["payload"]["agent_id"] == "reviewer-f014"


# --- Ontology ratify ---------------------------------------------------------


def _ratify_body(**overrides) -> dict:
    defaults = {
        "schema_json": {"entity_types": {"Company": {"properties": {}}}, "relationships": {}},
        "schema_modules": {"core": {"types": ["Company"]}},
        "source": "discovery",
        "reviewer": "ratifier-f014",
        "changelog": "F-014 test version",
    }
    defaults.update(overrides)
    return defaults


def test_ratify_emits_server_side_event(_db_rollback, client):
    with patch("src.elicitation.bridge.enqueue_event") as enqueue_mock:
        resp = client.post("/api/ontology/ratify", json=_ratify_body())
    assert resp.status_code == 200
    version = resp.json()
    enqueue_mock.assert_called_once()
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["event_type"] == "mcp_review_decided"
    assert kwargs["agent_id"] == "ratifier-f014"
    assert kwargs["payload"]["decision"] == "ratified"
    assert kwargs["payload"]["element_name"] == f"ontology_schema_v{version['version_number']}"
    assert kwargs["payload"]["agent_id"] == "ratifier-f014"


def test_ratify_survives_audit_failure(_db_rollback, client):
    with patch(
        "src.elicitation.bridge.enqueue_event",
        side_effect=RuntimeError("audit backend down"),
    ):
        resp = client.post("/api/ontology/ratify", json=_ratify_body())
    assert resp.status_code == 200


# --- Claims accept / reject --------------------------------------------------


@pytest.fixture()
def _clean_extraction_claims():
    """Wipe extraction_claims around claim tests (mirrors test_claim_routes)."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM extraction_claims"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM extraction_claims"))
        conn.commit()


def _seed_quarantined_claim() -> Claim:
    engine = get_engine()
    claim = Claim(
        claim_id=str(uuid4()),
        extraction_unit_id=f"unit-{uuid4().hex[:12]}",
        entity_type="Legal_Entity",
        relationship_type=None,
        subject_name="Acme Corp",
        predicate="entity",
        object_name=None,
        properties_json={"jurisdiction": "Delaware"},
        verdict=ClaimVerdict.REFUTED,
        status=ClaimStatus.QUARANTINED,
        decision_source="verifier",
        source_document_id="doc-f014",
        source_chunk_id="chunk-001",
        ontology_module="core",
        schema_version=1,
        extraction_event_id=str(uuid4()),
        created_at=datetime.now(UTC),
    )
    with SASession(get_engine()) as session:
        insert_claim(session, claim)
        session.commit()
    return claim


def _arcade_response() -> EntityCreateResponse:
    return EntityCreateResponse(
        grace_id=str(uuid4()),
        rid="#1:0",
        entity_type="Legal_Entity",
        created=True,
        canonical_match=False,
    )


def test_claim_accept_emits_server_side_event(client, _clean_extraction_claims):
    claim = _seed_quarantined_claim()
    with patch(
        "src.extraction.claim_override_writer.insert_entity",
        new=AsyncMock(return_value=_arcade_response()),
    ), patch("src.elicitation.bridge.enqueue_event") as enqueue_mock:
        resp = client.post(
            f"/api/claims/{claim.claim_id}/accept",
            json={"reviewer": "alice", "notes": "ok"},
            headers={"X-Graph-Scope": "all"},
        )
    assert resp.status_code == 200, resp.text
    enqueue_mock.assert_called_once()
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["event_type"] == "claim_disposition_accepted"
    assert kwargs["agent_id"] == "alice"
    payload = kwargs["payload"]
    assert payload["claim_id_hash"] == hashlib.sha256(
        claim.claim_id.encode("utf-8")).hexdigest()
    assert payload["reviewer_hash"] == hashlib.sha256(b"alice").hexdigest()
    assert payload["was_modified"] is False
    assert payload["ontology_module"] == "core"


def test_claim_reject_emits_server_side_event(client, _clean_extraction_claims):
    claim = _seed_quarantined_claim()
    with patch("src.elicitation.bridge.enqueue_event") as enqueue_mock:
        resp = client.post(
            f"/api/claims/{claim.claim_id}/reject",
            json={"reviewer": "bob", "notes": "not in scope"},
            headers={"X-Graph-Scope": "all"},
        )
    assert resp.status_code == 200, resp.text
    enqueue_mock.assert_called_once()
    kwargs = enqueue_mock.call_args.kwargs
    assert kwargs["event_type"] == "claim_disposition_rejected"
    assert kwargs["agent_id"] == "bob"
    payload = kwargs["payload"]
    assert payload["reviewer_hash"] == hashlib.sha256(b"bob").hexdigest()
    assert "was_modified" not in payload  # D234 rejected-payload has no such field


def test_claim_reject_survives_audit_failure(client, _clean_extraction_claims):
    claim = _seed_quarantined_claim()
    with patch(
        "src.elicitation.bridge.enqueue_event",
        side_effect=RuntimeError("audit backend down"),
    ):
        resp = client.post(
            f"/api/claims/{claim.claim_id}/reject",
            json={"reviewer": "bob"},
            headers={"X-Graph-Scope": "all"},
        )
    assert resp.status_code == 200


# --- Bridge agent-context passthrough (real enqueue, mocked writer) ----------


def test_bridge_carries_agent_context_onto_envelope():
    """The extended bridge kwargs land on the D364 envelope agent columns."""
    from src.elicitation.bridge import enqueue_event

    with patch("src.elicitation.bridge.write_event") as write_mock, \
            patch("src.elicitation.bridge.get_session_factory"):
        enqueue_event(
            event_type="claim_disposition_rejected",
            payload={
                "claim_id_hash": "a" * 64,
                "reviewer_hash": "b" * 64,
                "ontology_module": "core",
            },
            agent_id="reviewer-f014",
            delegation_source="user_direct",
        )
    write_mock.assert_called_once()
    envelope = write_mock.call_args[0][1]
    assert envelope.actor_type == "system"  # server-emitted marker
    assert envelope.agent_id == "reviewer-f014"
    assert envelope.delegation_source == "user_direct"
