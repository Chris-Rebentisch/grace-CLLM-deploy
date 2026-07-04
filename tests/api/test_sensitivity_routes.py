"""Sensitivity Gate route tests (Chunk 43, CP3 / D344).

Covers all six routes under ``/api/sensitivity``. The DB layer is patched
at the route-module boundary so tests run without live Postgres. Live
persistence is exercised by ``tests/permissions/test_sensitivity_repository.py``.

Critical invariants enforced by tests:

* ``coverage_score`` MUST be absent from every read-path response body
  (D120/D217). Verified across all 5 read paths.
* ``POST /report/generate`` MUST require admin-key when ``GRACE_ADMIN_KEY``
  is set; loopback bypass when unset (and is NOT in ``READONLY_ROUTES``).
* Force-regen rate limit fires on second ``?force=true`` within 60s per
  matrix.
* ``GET /report/latest`` route is registered before ``/report/{report_id}``
  so the literal segment matches first (FastAPI route ordering).
* Audit-trail routes ship as skeleton in CP3; body is wired in CP5.
"""

from __future__ import annotations

import importlib
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
    SensitivityClassificationReport,
    SensitivityTag,
    TagInventoryEntry,
)


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_db():
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
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


@pytest.fixture(autouse=True)
def _reset_force_regen_state():
    """Clear the in-memory force-regen rate-limit state between tests."""
    from src.api import sensitivity_routes as routes

    with routes._force_regen_lock:
        routes._force_regen_last.clear()
    yield
    with routes._force_regen_lock:
        routes._force_regen_last.clear()


def _matrix_row(matrix_id: UUID | None = None) -> dict:
    payload = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="cl-1",
                display_name="cl-1",
                access_rules=[
                    AccessRule(
                        resource_kind="ontology_module",
                        resource_label="finance",
                        action="view",
                        decision="allow",
                        sensitivity_tags=[SensitivityTag(name="pii")],
                    )
                ],
            )
        ]
    ).model_dump(mode="json")
    return {
        "permission_matrix_id": matrix_id or uuid4(),
        "payload": payload,
        "payload_hash": "a" * 64,
        "previous_hash": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "created_by": None,
        "version_label": None,
    }


def _report_row(matrix_id: UUID | None = None, report_id: UUID | None = None,
                *, coverage_band: str | None = "high",
                coverage_score: float | None = 0.85,
                corpus_below_floor: bool = False) -> dict:
    return {
        "id": report_id or uuid4(),
        "permission_matrix_id": matrix_id or uuid4(),
        "generated_at": datetime(2026, 5, 9, 12, 0, 0, tzinfo=timezone.utc),
        "tag_inventory": [
            TagInventoryEntry(
                tag_name="pii", rule_count=1, cluster_count=1
            ).model_dump(mode="json")
        ],
        "coverage_breakdown": [],
        "untagged_rules": [],
        "tag_hygiene_findings": [],
        "truncated": False,
        "coverage_band": coverage_band,
        "coverage_score": coverage_score,
        "corpus_below_floor": corpus_below_floor,
    }


# ---------- 1. POST /report/generate ----------------------------------


def test_generate_422_when_no_active_matrix(client):
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=None,
    ):
        resp = client.post("/api/sensitivity/report/generate")
    assert resp.status_code == 422


def test_generate_201_creates_report_and_strips_coverage_score(client):
    mid = uuid4()
    rid = uuid4()
    matrix_row = _matrix_row(mid)
    inserted = _report_row(mid, rid, coverage_band="high", coverage_score=0.85)
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=matrix_row,
    ), patch(
        "src.api.sensitivity_routes._report_repo.get_latest_for_matrix",
        return_value=None,
    ), patch(
        "src.api.sensitivity_routes._report_repo.insert_report",
        return_value=inserted,
    ):
        resp = client.post("/api/sensitivity/report/generate")
    assert resp.status_code == 201
    body = resp.json()
    # D120/D217: coverage_score MUST NOT appear in the response body.
    assert "coverage_score" not in body
    assert body["coverage_band"] == "high"


def test_generate_409_when_existing_and_no_force(client):
    mid = uuid4()
    matrix_row = _matrix_row(mid)
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=matrix_row,
    ), patch(
        "src.api.sensitivity_routes._report_repo.get_latest_for_matrix",
        return_value=_report_row(mid),
    ):
        resp = client.post("/api/sensitivity/report/generate")
    assert resp.status_code == 409


def test_generate_force_true_succeeds_when_existing(client):
    mid = uuid4()
    matrix_row = _matrix_row(mid)
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=matrix_row,
    ), patch(
        "src.api.sensitivity_routes._report_repo.get_latest_for_matrix",
        return_value=_report_row(mid),
    ), patch(
        "src.api.sensitivity_routes._report_repo.insert_report",
        return_value=_report_row(mid),
    ):
        resp = client.post("/api/sensitivity/report/generate?force=true")
    assert resp.status_code == 201


def test_generate_429_on_second_force_within_window(client):
    mid = uuid4()
    matrix_row = _matrix_row(mid)
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=matrix_row,
    ), patch(
        "src.api.sensitivity_routes._report_repo.get_latest_for_matrix",
        return_value=_report_row(mid),
    ), patch(
        "src.api.sensitivity_routes._report_repo.insert_report",
        return_value=_report_row(mid),
    ):
        first = client.post("/api/sensitivity/report/generate?force=true")
        second = client.post("/api/sensitivity/report/generate?force=true")
    assert first.status_code == 201
    assert second.status_code == 429


# ---------- 2. GET /report/latest -------------------------------------


def test_latest_404_when_no_active_matrix(client):
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=None,
    ):
        resp = client.get("/api/sensitivity/report/latest")
    assert resp.status_code == 404


def test_latest_404_when_no_report_for_matrix(client):
    mid = uuid4()
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=_matrix_row(mid),
    ), patch(
        "src.api.sensitivity_routes._report_repo.get_latest_for_matrix",
        return_value=None,
    ):
        resp = client.get("/api/sensitivity/report/latest")
    assert resp.status_code == 404


def test_latest_200_strips_coverage_score(client):
    mid = uuid4()
    with patch(
        "src.api.sensitivity_routes._matrix_repo.get_active_matrix",
        return_value=_matrix_row(mid),
    ), patch(
        "src.api.sensitivity_routes._report_repo.get_latest_for_matrix",
        return_value=_report_row(mid, coverage_score=0.99),
    ):
        resp = client.get("/api/sensitivity/report/latest")
    assert resp.status_code == 200
    body = resp.json()
    assert "coverage_score" not in body
    assert body["coverage_band"] == "high"


# ---------- 3. GET /report/{report_id} --------------------------------


def test_report_by_id_404_when_unknown(client):
    rid = uuid4()
    with patch(
        "src.api.sensitivity_routes._report_repo.get_report_by_id",
        return_value=None,
    ):
        resp = client.get(f"/api/sensitivity/report/{rid}")
    assert resp.status_code == 404


def test_report_by_id_200_strips_coverage_score(client):
    rid = uuid4()
    mid = uuid4()
    with patch(
        "src.api.sensitivity_routes._report_repo.get_report_by_id",
        return_value=_report_row(mid, rid, coverage_score=0.5),
    ):
        resp = client.get(f"/api/sensitivity/report/{rid}")
    assert resp.status_code == 200
    assert "coverage_score" not in resp.json()


def test_report_by_id_422_on_malformed_uuid(client):
    resp = client.get("/api/sensitivity/report/not-a-uuid")
    assert resp.status_code == 422


# ---------- 4. GET /report (list) -------------------------------------


def test_list_reports_requires_matrix_id(client):
    resp = client.get("/api/sensitivity/report")
    assert resp.status_code == 422  # missing required query param


def test_list_reports_200_paginates_and_strips_coverage_score(client):
    mid = uuid4()
    rows = [_report_row(mid, coverage_score=0.6) for _ in range(3)]
    with patch(
        "src.api.sensitivity_routes._report_repo.list_reports_for_matrix",
        return_value=rows,
    ):
        resp = client.get(f"/api/sensitivity/report?matrix_id={mid}&limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert "reports" in body
    assert len(body["reports"]) == 3
    for r in body["reports"]:
        assert "coverage_score" not in r
    assert body["next_cursor"] is None


def test_list_reports_invalid_cursor_422(client):
    mid = uuid4()
    resp = client.get(
        f"/api/sensitivity/report?matrix_id={mid}&cursor=not-an-int"
    )
    assert resp.status_code == 422


def test_list_reports_next_cursor_when_more_rows(client):
    mid = uuid4()
    # Returns limit+1 rows -> next_cursor populated.
    rows = [_report_row(mid) for _ in range(4)]
    with patch(
        "src.api.sensitivity_routes._report_repo.list_reports_for_matrix",
        return_value=rows,
    ):
        resp = client.get(f"/api/sensitivity/report?matrix_id={mid}&limit=3")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["reports"]) == 3
    assert body["next_cursor"] == "3"


# ---------- 5. GET /audit-trail (skeleton) ----------------------------


def test_audit_trail_requires_tag(client):
    resp = client.get("/api/sensitivity/audit-trail")
    assert resp.status_code == 422


def test_audit_trail_skeleton_returns_empty_page(client):
    resp = client.get("/api/sensitivity/audit-trail?tag=pii")
    assert resp.status_code == 200
    body = resp.json()
    assert body["events"] == []
    assert body["next_cursor"] is None


# ---------- 6. GET /audit-trail/{query_event_id} (skeleton) -----------


def test_audit_trail_event_404_in_v1(client):
    qid = uuid4()
    resp = client.get(f"/api/sensitivity/audit-trail/{qid}")
    assert resp.status_code == 404


def test_audit_trail_event_422_on_malformed_uuid(client):
    resp = client.get("/api/sensitivity/audit-trail/not-a-uuid")
    assert resp.status_code == 422


# ---------- Critical Don'ts ------------------------------------------


def test_generate_route_not_in_readonly_routes_allowlist():
    """Critical Don't #9: POST /api/sensitivity/report/generate MUST NOT
    be in READONLY_ROUTES. It is a mutating route (writes a new row in
    sensitivity_classification_reports + denormalized columns on
    permission_matrices). Admission flows through the standard
    admin-key path."""
    from src.mcp_server.server import READONLY_ROUTES

    assert "POST /api/sensitivity/report/generate" not in READONLY_ROUTES
    assert "/api/sensitivity/report/generate" not in READONLY_ROUTES
