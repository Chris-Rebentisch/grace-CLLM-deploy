"""Change Directive API route tests (CP6, D295/D296)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.api.main import app
from src.change_directives.evidence_criterion import CompileResult
from src.change_directives.repository import insert_realization_snapshot
from src.shared.config import get_settings


@pytest.fixture(scope="module")
def engine():
    settings = get_settings()
    eng = create_engine(settings.database_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def client(engine):
    created_ids: list[str] = []
    cli = TestClient(app)
    cli._test_created_ids = created_ids  # type: ignore[attr-defined]
    yield cli
    if created_ids:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('alembic.downgrading','true', true)")
            )
            conn.execute(
                text(
                    "DELETE FROM change_directive_realization_snapshots "
                    "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": created_ids},
            )
            conn.execute(
                text(
                    "DELETE FROM change_directive_evidence_criteria "
                    "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": created_ids},
            )
            conn.execute(
                text(
                    "DELETE FROM change_directive_state_transitions "
                    "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": created_ids},
            )
            conn.execute(
                text(
                    "DELETE FROM change_directives "
                    "WHERE directive_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": created_ids},
            )


def _track(client, did: str) -> None:
    client._test_created_ids.append(did)


def _author_headers(uid):
    return {"X-Requesting-User": str(uid)}


def _oa_body(**overrides):
    body = {
        "tier": "Operational_Adjustment",
        "title": "Adopt v2 onboarding form",
        "description": "Roll out v2 form by EoQ.",
        "affected_segments": ["operations"],
    }
    body.update(overrides)
    return body


def test_post_creates_directive_in_draft(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    _track(client, body["directive_id"])
    assert body["status"] == "draft"
    assert body["tier"] == "Operational_Adjustment"
    assert str(body["authored_by"]) == str(author)


def test_get_visibility_filters_other_users(client) -> None:
    author = uuid4()
    intruder = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(visibility="private_to_self"),
        headers=_author_headers(author),
    )
    assert resp.status_code == 201
    did = resp.json()["directive_id"]
    _track(client, did)

    # Author can see it.
    r1 = client.get(
        f"/api/change-directives/{did}", headers=_author_headers(author)
    )
    assert r1.status_code == 200
    # Intruder gets 404 (visibility-filtered, not 403, to avoid leakage).
    r2 = client.get(
        f"/api/change-directives/{did}", headers=_author_headers(intruder)
    )
    assert r2.status_code == 404


def test_patch_draft_happy_path(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    r = client.patch(
        f"/api/change-directives/{did}",
        json={"title": "renamed", "description": "updated"},
        headers=_author_headers(author),
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "renamed"
    assert r.json()["status"] == "draft"


def test_patch_attempting_status_mutation_returns_422(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    r = client.patch(
        f"/api/change-directives/{did}",
        json={"status": "active"},
        headers=_author_headers(author),
    )
    assert r.status_code == 422


def test_patch_attempting_visibility_mutation_returns_422(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    r = client.patch(
        f"/api/change-directives/{did}",
        json={"visibility": "permission_matrix_default"},
        headers=_author_headers(author),
    )
    assert r.status_code == 422


def test_transition_happy_path(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    r = client.post(
        f"/api/change-directives/{did}/transition",
        json={"to_state": "active", "reason": "ratified"},
        headers=_author_headers(author),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"


def test_transition_illegal_returns_422(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    r = client.post(
        f"/api/change-directives/{did}/transition",
        json={"to_state": "realized"},
        headers=_author_headers(author),
    )
    assert r.status_code == 422


def test_patch_after_transition_returns_422(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    client.post(
        f"/api/change-directives/{did}/transition",
        json={"to_state": "active"},
        headers=_author_headers(author),
    )
    r = client.patch(
        f"/api/change-directives/{did}",
        json={"title": "too late"},
        headers=_author_headers(author),
    )
    assert r.status_code == 422


def test_criterion_authoring_round_trip(client) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)

    # Mock the compile orchestrator to avoid Ollama dependency.
    fake = CompileResult(
        compiled_query="MATCH (n:Legal_Entity) RETURN n",
        compilation_status="proposed",
        error_detail=None,
    )

    async def _fake_compile(*args, **kwargs):  # noqa: ARG001
        return fake

    with patch(
        "src.change_directives.routes.compile_evidence_criterion",
        side_effect=_fake_compile,
    ):
        r = client.post(
            f"/api/change-directives/{did}/criteria",
            json={"natural_language": "Count Legal_Entity nodes"},
            headers=_author_headers(author),
        )
    assert r.status_code == 201, r.text
    cid = r.json()["criterion_id"]
    assert r.json()["compilation_status"] == "proposed"
    assert r.json()["compiled_query"] == "MATCH (n:Legal_Entity) RETURN n"

    # Approve.
    r2 = client.patch(
        f"/api/change-directives/{did}/criteria/{cid}",
        json={"action": "approve"},
        headers=_author_headers(author),
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["compilation_status"] == "approved"

    # Manual override with a new query.
    r3 = client.patch(
        f"/api/change-directives/{did}/criteria/{cid}",
        json={"action": "manual_override", "compiled_query": "MATCH (m) RETURN m"},
        headers=_author_headers(author),
    )
    assert r3.status_code == 200
    assert r3.json()["compilation_status"] == "manually_authored"
    assert r3.json()["compiled_query"] == "MATCH (m) RETURN m"


def test_get_latest_snapshot_route(client, engine) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    client.post(
        f"/api/change-directives/{did}/transition",
        json={"to_state": "active", "reason": "go"},
        headers=_author_headers(author),
    )
    cid = str(uuid4())
    crit = [
        {
            "criterion_id": cid,
            "satisfied": True,
            "measured_value": 1.0,
            "query_executed_at": datetime.now(timezone.utc).isoformat(),
            "result_hash": "aa" * 32,
            "sample_grace_ids": [],
        }
    ]
    with Session(bind=engine) as db:
        insert_realization_snapshot(
            db,
            directive_id=UUID(did),
            snapshot_at=datetime(2026, 5, 7, 15, 0, tzinfo=timezone.utc),
            criteria_results=crit,
            progress_percentage=0.5,
            velocity=0.02,
            evidence_count_consistent=None,
            evidence_count_counter=None,
            first_evidence_seen_at=None,
            last_counter_evidence_seen_at=None,
            criteria_all_satisfied=None,
        )

    r = client.get(
        f"/api/change-directives/{did}/snapshot",
        headers=_author_headers(author),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["directive_id"] == did
    assert body["progress_percentage"] is not None


def test_list_snapshots_route(client, engine) -> None:
    author = uuid4()
    resp = client.post(
        "/api/change-directives",
        json=_oa_body(),
        headers=_author_headers(author),
    )
    did = resp.json()["directive_id"]
    _track(client, did)
    client.post(
        f"/api/change-directives/{did}/transition",
        json={"to_state": "active", "reason": "go"},
        headers=_author_headers(author),
    )
    cid = str(uuid4())
    crit = [
        {
            "criterion_id": cid,
            "satisfied": False,
            "query_executed_at": datetime.now(timezone.utc).isoformat(),
            "result_hash": "bb" * 32,
            "sample_grace_ids": [],
        }
    ]
    with Session(bind=engine) as db:
        for hour in (10, 11):
            insert_realization_snapshot(
                db,
                directive_id=UUID(did),
                snapshot_at=datetime(
                    2026, 5, 8, hour, 0, tzinfo=timezone.utc
                ),
                criteria_results=crit,
                progress_percentage=0.3,
                velocity=None,
                evidence_count_consistent=None,
                evidence_count_counter=None,
                first_evidence_seen_at=None,
                last_counter_evidence_seen_at=None,
                criteria_all_satisfied=None,
            )

    r = client.get(
        f"/api/change-directives/{did}/snapshots?limit=10",
        headers=_author_headers(author),
    )
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list)
    assert len(items) == 2
