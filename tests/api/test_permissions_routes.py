"""Permissions module route tests (Chunk 42, CP8, D246 mirror).

Covers all 10 route groups under ``/api/permissions``. DB layer is
patched at the route-module boundary so these tests run without live
Postgres. Live persistence is exercised separately by
``tests/permissions/test_repository.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.permissions.enforcer import rebuild_enforcer
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
)
from src.permissions.principal_context import User


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_db():
    """Override ``get_db`` so route handlers don't hit Postgres."""
    from src.shared.database import get_db

    fake_session = MagicMock()
    fake_session.execute = MagicMock()
    fake_session.commit = MagicMock()
    fake_session.rollback = MagicMock()

    def _override():
        yield fake_session

    app.dependency_overrides[get_db] = _override
    yield fake_session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def _reset_enforcer():
    """The enforcer is process-global; reset between tests."""
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


def _matrix_row(
    matrix_id: UUID | None = None,
    *,
    payload_hash: str = "a" * 64,
    previous_hash: str | None = None,
) -> dict:
    return {
        "permission_matrix_id": matrix_id or uuid4(),
        "payload": PermissionMatrix().model_dump(mode="json"),
        "payload_hash": payload_hash,
        "previous_hash": previous_hash,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "created_by": None,
        "version_label": None,
    }


# ---------- 1. GET /matrix/active ----------


def test_get_active_matrix_404_when_none(client):
    with patch(
        "src.api.permissions_routes._matrix_repo.get_active_matrix",
        return_value=None,
    ):
        resp = client.get("/api/permissions/matrix/active")
    assert resp.status_code == 404


def test_get_active_matrix_200_serializes_uuids(client):
    mid = uuid4()
    with patch(
        "src.api.permissions_routes._matrix_repo.get_active_matrix",
        return_value=_matrix_row(mid),
    ):
        resp = client.get("/api/permissions/matrix/active")
    assert resp.status_code == 200
    body = resp.json()
    assert body["permission_matrix_id"] == str(mid)
    assert body["payload_hash"] == "a" * 64


# ---------- 2. GET /matrix/versions ----------


def test_list_matrix_versions_paginated(client):
    rows = [_matrix_row() for _ in range(3)]
    with patch(
        "src.api.permissions_routes._matrix_repo.get_matrix_versions",
        return_value=rows,
    ):
        resp = client.get("/api/permissions/matrix/versions?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert "versions" in body
    assert "next_cursor" in body
    assert len(body["versions"]) == 3


def test_list_matrix_versions_invalid_cursor_422(client):
    resp = client.get("/api/permissions/matrix/versions?cursor=not-an-int")
    assert resp.status_code == 422


# ---------- 3. GET /matrix/{matrix_id} ----------


def test_get_matrix_by_id_404(client):
    with patch(
        "src.api.permissions_routes._matrix_repo.get_matrix_by_id",
        return_value=None,
    ):
        resp = client.get(f"/api/permissions/matrix/{uuid4()}")
    assert resp.status_code == 404


def test_get_matrix_by_id_200(client):
    mid = uuid4()
    with patch(
        "src.api.permissions_routes._matrix_repo.get_matrix_by_id",
        return_value=_matrix_row(mid),
    ):
        resp = client.get(f"/api/permissions/matrix/{mid}")
    assert resp.status_code == 200
    assert resp.json()["permission_matrix_id"] == str(mid)


# ---------- 4. GET /matrix/verify-chain ----------


def test_verify_chain_returns_status(client):
    """Verify-chain literal path must take precedence over /{matrix_id}."""
    with patch(
        "src.api.permissions_routes._matrix_repo.verify_chain",
        return_value={"valid": True, "chain_length": 0, "broken_at": None},
    ):
        resp = client.get("/api/permissions/matrix/verify-chain")
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is True
    assert body["chain_length"] == 0


# ---------- 5. POST /matrix/hypothesis/generate ----------


def test_hypothesis_generate_returns_202_and_run_id(client):
    """The trigger route INSERTs a placeholder row, spawns the CLI, and
    returns 202 + run_id. Subprocess is mocked so the test never spawns
    a real Python child."""
    rid = uuid4()
    eid = uuid4()
    with patch(
        "src.api.permissions_routes._insert_hypothesis_run_placeholder",
        return_value={"run_id": rid, "evidence_id": eid},
    ), patch(
        "src.api.permissions_routes.subprocess.Popen",
    ) as popen_mock:
        proc = MagicMock()
        proc.pid = 12345
        popen_mock.return_value = proc
        resp = client.post(
            "/api/permissions/matrix/hypothesis/generate",
            json={"evidence_id": str(eid), "dry_run": True},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"] == str(rid)
    assert body["evidence_id"] == str(eid)
    assert body["pid"] == 12345
    # Verify subprocess was called with start_new_session=True (D246).
    args, kwargs = popen_mock.call_args
    assert kwargs.get("start_new_session") is True
    cmd = args[0]
    assert "--run-id" in cmd
    assert str(rid) in cmd


def test_hypothesis_generate_409_when_evidence_has_running_run(client, _stub_db):
    """IntegrityError on commit maps to 409 (concurrent in-flight guard, DV4)."""
    from sqlalchemy.exc import IntegrityError

    rid = uuid4()
    eid = uuid4()
    _stub_db.commit = MagicMock(
        side_effect=IntegrityError("stmt", {}, Exception("uq running evidence"))
    )

    with patch(
        "src.api.permissions_routes._insert_hypothesis_run_placeholder",
        return_value={"run_id": rid, "evidence_id": eid},
    ), patch(
        "src.api.permissions_routes.subprocess.Popen",
    ) as popen_mock:
        popen_mock.return_value = MagicMock(pid=1)
        resp = client.post(
            "/api/permissions/matrix/hypothesis/generate",
            json={"evidence_id": str(eid), "dry_run": True},
        )
    assert resp.status_code == 409
    popen_mock.assert_not_called()


def test_hypothesis_generate_422_on_bad_evidence_id(client):
    resp = client.post(
        "/api/permissions/matrix/hypothesis/generate",
        json={"evidence_id": "not-a-uuid"},
    )
    assert resp.status_code == 422


# ---------- 6. GET /hypothesis/{run_id} ----------


def test_get_hypothesis_run_404(client):
    with patch(
        "src.api.permissions_routes._get_hypothesis_run",
        return_value=None,
    ):
        resp = client.get(f"/api/permissions/hypothesis/{uuid4()}")
    assert resp.status_code == 404


def test_get_hypothesis_run_200_serializes(client):
    rid = uuid4()
    eid = uuid4()
    row = {
        "run_id": rid,
        "evidence_id": eid,
        "status": "completed",
        "hypothesis_set": {"hypotheses": []},
        "operator": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "completed_at": None,
    }
    with patch(
        "src.api.permissions_routes._get_hypothesis_run",
        return_value=row,
    ):
        resp = client.get(f"/api/permissions/hypothesis/{rid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == str(rid)
    assert body["evidence_id"] == str(eid)
    assert body["status"] == "completed"


# ---------- 7. POST /matrix/ratify ----------


def _seed_ratify_matrix(user_id: UUID) -> None:
    """Seed the enforcer with a matrix that grants the given user_id
    permission to ratify. Pre-existing matrix is required because the
    PermissionMatrixMiddleware enforces default-deny on no-active-matrix
    (CP6 — `test_mutating_route_with_no_matrix_returns_403`)."""
    seed = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="cluster_ratifiers",
                display_name="ratifiers",
                members=[RoleClusterMember(person_grace_id=str(user_id))],
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="/api/permissions/matrix/ratify",
                        action="ratify",
                        decision="allow",
                    )
                ],
            )
        ],
        default_decision="deny",
    )
    rebuild_enforcer(seed)


def test_ratify_creates_201_and_rebuilds_enforcer(client):
    mid = uuid4()
    user_id = uuid4()
    _seed_ratify_matrix(user_id)
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="cluster_alpha",
                display_name="cluster_alpha",
                members=[RoleClusterMember(person_grace_id="person-1")],
            ),
        ],
    )
    fake_row = {
        "permission_matrix_id": mid,
        "payload_hash": "b" * 64,
        "previous_hash": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "created_by": "alice",
        "version_label": "v0",
    }
    with patch(
        "src.permissions.api_middleware.from_admission_tree",
        return_value=User(user_id=user_id),
    ), patch(
        "src.api.permissions_routes._matrix_repo.insert_matrix",
        return_value=fake_row,
    ) as insert_mock:
        resp = client.post(
            "/api/permissions/matrix/ratify",
            json={
                "matrix": matrix.model_dump(mode="json"),
                "created_by": "alice",
                "version_label": "v0",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["permission_matrix_id"] == str(mid)
    assert body["payload_hash"] == "b" * 64
    insert_mock.assert_called_once()
    # Enforcer must be rebuilt with the ratified matrix.
    from src.permissions.enforcer import get_enforcer
    assert get_enforcer().matrix is not None


def test_ratify_409_on_db_failure(client):
    from sqlalchemy.exc import DBAPIError

    user_id = uuid4()
    _seed_ratify_matrix(user_id)
    matrix = PermissionMatrix()
    with patch(
        "src.permissions.api_middleware.from_admission_tree",
        return_value=User(user_id=user_id),
    ), patch(
        "src.api.permissions_routes._matrix_repo.insert_matrix",
        side_effect=DBAPIError("INSERT", {}, Exception("boom")),
    ):
        resp = client.post(
            "/api/permissions/matrix/ratify",
            json={"matrix": matrix.model_dump(mode="json")},
        )
    assert resp.status_code == 409


# ---------- 8. GET /evidence/{evidence_id} ----------


def test_get_evidence_returns_bundle_shape(client):
    eid = uuid4()
    resp = client.get(f"/api/permissions/evidence/{eid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["evidence_id"] == str(eid)
    assert "sections" in body


# ---------- 9. POST /drift/run ----------


def test_drift_run_returns_202_and_job_id(client):
    with patch(
        "src.api.permissions_routes.subprocess.Popen",
    ) as popen_mock:
        proc = MagicMock()
        proc.pid = 22222
        popen_mock.return_value = proc
        resp = client.post(
            "/api/permissions/drift/run",
            json={"dry_run": True},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body
    UUID(body["job_id"])  # parse-validates
    assert body["pid"] == 22222


# ---------- 10. GET /drift/queue ----------


def test_drift_queue_returns_paged_envelope(client):
    sample_row = {
        "drift_queue_id": str(uuid4()),
        "person_grace_id": "person-1",
        "proposed_cluster_id": None,
        "drift_band": "high",
        "status": "pending",
        "operator_decision": None,
        "rationale": None,
        "details": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc).isoformat(),
        "decided_at": None,
    }
    with patch(
        "src.api.permissions_routes._list_drift_queue",
        return_value=([sample_row], None),
    ):
        resp = client.get("/api/permissions/drift/queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["queue"] == [sample_row]
    assert body["next_cursor"] is None


def test_drift_queue_invalid_band_422(client):
    resp = client.get("/api/permissions/drift/queue?drift_band=nope")
    assert resp.status_code == 422


def test_drift_queue_forwards_query_filters(client):
    with patch(
        "src.api.permissions_routes._list_drift_queue",
        return_value=([], None),
    ) as list_mock:
        resp = client.get(
            "/api/permissions/drift/queue?drift_band=high&status=pending"
        )
    assert resp.status_code == 200
    assert list_mock.call_count == 1
    kwargs = list_mock.call_args.kwargs
    assert kwargs["drift_band"] == "high"
    assert kwargs["status_filter"] == "pending"
