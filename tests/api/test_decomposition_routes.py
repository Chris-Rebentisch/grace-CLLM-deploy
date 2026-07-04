"""Decomposition module route tests (Chunk 41, CP8, D328).

Covers all 10 route groups (11 endpoints) under ``/api/decomposition``.
DB layer is patched at the route-module boundary so these tests run
without live Postgres. Live persistence is exercised separately by
``tests/decomposition/test_layer5_decision.py`` and
``tests/decomposition/test_layer5_layer6_persistence.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_db():
    """Override ``get_db`` so route handlers don't hit Postgres.

    Each test patches the helpers it needs through; the session is just
    a MagicMock that records calls.
    """
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


def _run_row(run_id: UUID | None = None, **overrides) -> dict:
    """Build a minimal run row dict matching ``run_repository.get_run`` shape."""
    rid = run_id or uuid4()
    base = {
        "run_id": rid,
        "archive_root": "/tmp/archive",
        "archive_root_canonical_hash": "a" * 64,
        "started_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "completed_at": None,
        "status": "running",
        "total_documents": None,
        "operator": None,
        "resumed_from_run_id": None,
        "layer1_summary": None,
        "layer2_decision": None,
        "layer3_decision": None,
        "layer4_hypotheses": None,
        "layer5_decision": None,
        "layer6_validation": None,
        "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


# ---------- 1. GET /runs ----------


def test_list_runs_returns_paged_envelope(client):
    with patch(
        "src.api.decomposition_routes._list_runs",
        return_value=([_run_row()], None),
    ):
        resp = client.get("/api/decomposition/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert "next_cursor" in body
    assert len(body["runs"]) == 1


# ---------- 2. GET /runs/{run_id} ----------


def test_get_run_detail_404_when_missing(client):
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=None,
    ):
        resp = client.get(f"/api/decomposition/runs/{uuid4()}")
    assert resp.status_code == 404


def test_get_run_detail_serializes_uuids_and_datetimes(client):
    rid = uuid4()
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ):
        resp = client.get(f"/api/decomposition/runs/{rid}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == str(rid)
    assert body["archive_root"] == "/tmp/archive"
    assert isinstance(body["started_at"], str)


# ---------- 3. GET /runs/{run_id}/layer4/hypotheses ----------


def test_layer4_hypotheses_404_when_layer4_missing(client):
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(layer4_hypotheses=None),
    ):
        resp = client.get(f"/api/decomposition/runs/{uuid4()}/layer4/hypotheses")
    assert resp.status_code == 404


def test_layer4_hypotheses_returns_payload(client):
    payload = {"hypotheses": [{"name": "H1", "kind": "segmented"}]}
    rid = uuid4()
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid, layer4_hypotheses=payload),
    ):
        resp = client.get(f"/api/decomposition/runs/{rid}/layer4/hypotheses")
    assert resp.status_code == 200
    body = resp.json()
    assert body["run_id"] == str(rid)
    assert body["layer4_hypotheses"] == payload


# ---------- 4. POST /runs/trigger ----------


def test_trigger_run_422_when_archive_missing(client, tmp_path):
    resp = client.post(
        "/api/decomposition/runs/trigger",
        json={"archive_root": str(tmp_path / "does-not-exist")},
    )
    assert resp.status_code == 422


def test_trigger_run_409_when_concurrent_run_in_progress(client, tmp_path):
    archive = tmp_path / "a"
    archive.mkdir()
    in_progress = {
        "run_id": uuid4(),
        "archive_root": str(archive),
        "archive_root_canonical_hash": "h",
        "started_at": datetime.now(timezone.utc),
        "status": "running",
    }
    with patch(
        "src.api.decomposition_routes._running_run_for_archive_hash",
        return_value=in_progress,
    ):
        resp = client.post(
            "/api/decomposition/runs/trigger",
            json={"archive_root": str(archive)},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert "in progress" in str(body["detail"]).lower() or body["detail"]["error"]


def test_trigger_run_returns_202_and_spawns_subprocess(client, tmp_path):
    archive = tmp_path / "a"
    archive.mkdir()
    rid = uuid4()
    with patch(
        "src.api.decomposition_routes._running_run_for_archive_hash",
        return_value=None,
    ), patch(
        "src.api.decomposition_routes._run_repository.create_run",
        return_value=_run_row(run_id=rid, archive_root=str(archive)),
    ), patch(
        "src.api.decomposition_routes._spawn_decomposition_cli",
        return_value=99999,
    ) as p_spawn:
        resp = client.post(
            "/api/decomposition/runs/trigger",
            json={"archive_root": str(archive)},
        )
    assert resp.status_code == 202
    body = resp.json()
    assert body["run_id"] == str(rid)
    assert body["pid"] == 99999
    p_spawn.assert_called_once()


def test_trigger_run_passes_placeholder_run_id_to_spawn(client, tmp_path):
    """F-030 / ISS-0014: the spawned CLI must receive the placeholder row id
    (``--run-id``) so the id returned by the 202 IS the executing run —
    previously the CLI INSERTed its own row and the placeholder stayed
    'running' forever."""
    from src.api.decomposition_routes import _build_decomposition_argv

    archive = tmp_path / "a"
    archive.mkdir()
    rid = uuid4()
    with patch(
        "src.api.decomposition_routes._running_run_for_archive_hash",
        return_value=None,
    ), patch(
        "src.api.decomposition_routes._run_repository.create_run",
        return_value=_run_row(run_id=rid, archive_root=str(archive)),
    ), patch(
        "src.api.decomposition_routes._spawn_decomposition_cli",
        return_value=12345,
    ) as p_spawn:
        resp = client.post(
            "/api/decomposition/runs/trigger",
            json={"archive_root": str(archive), "limit": 2},
        )
    assert resp.status_code == 202
    assert p_spawn.call_args.kwargs["run_id"] == rid
    # And the argv builder places the id after --run-id in the spawned argv.
    argv = _build_decomposition_argv(str(archive), run_id=rid, limit=2)
    assert argv[argv.index("--run-id") + 1] == str(rid)


# ---------- 5. POST /runs/{run_id}/layer5/decision ----------


def test_layer5_decision_404_when_run_missing(client):
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=None,
    ):
        resp = client.post(
            f"/api/decomposition/runs/{uuid4()}/layer5/decision",
            json={
                "decision_kind": "accepted_null",
                "rationale": "",
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "modifications": [],
            },
        )
    assert resp.status_code == 404


def test_layer5_decision_happy_path_emits_telemetry(client):
    rid = uuid4()
    out = _run_row(
        run_id=rid,
        layer5_decision={"decision_kind": "accepted_null"},
    )
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._layer5_decision_mod.record_layer5_decision",
        return_value=out,
    ), patch(
        "src.api.decomposition_routes.record_decomposition_layer5_decision"
    ) as p_metric, patch(
        "src.api.decomposition_routes._emit_elicitation_event"
    ) as p_event:
        resp = client.post(
            f"/api/decomposition/runs/{rid}/layer5/decision",
            json={
                "decision_kind": "accepted_null",
                "rationale": "",
                "decided_at": datetime.now(timezone.utc).isoformat(),
                "modifications": [],
            },
        )
    assert resp.status_code == 200, resp.text
    p_metric.assert_called_once_with(decision_kind="accepted_null")
    p_event.assert_called_once()
    assert p_event.call_args.args[0] == "decomposition_layer5_decision_recorded"


# ---------- 6. POST /runs/{run_id}/rerun ----------


def test_rerun_409_when_cap_exceeded(client):
    from src.decomposition.rerun_repository import RerunCapExceededError

    rid = uuid4()
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._rerun_repository.create_rerun_run",
        side_effect=RerunCapExceededError("cap exceeded"),
    ):
        resp = client.post(
            f"/api/decomposition/runs/{rid}/rerun",
            json={"direction": "finer"},
        )
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["error"] == "rerun cap exceeded"
    assert body["detail"]["hard_cap"] == 5


def test_rerun_happy_path_201_emits_telemetry(client):
    rid = uuid4()
    new_rid = uuid4()
    new_row = _run_row(run_id=new_rid)
    new_row["lineage_depth"] = 2
    new_row["direction"] = "finer"
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._rerun_repository.create_rerun_run",
        return_value=new_row,
    ), patch(
        "src.api.decomposition_routes.record_decomposition_rerun"
    ) as p_metric, patch(
        "src.api.decomposition_routes._emit_elicitation_event"
    ) as p_event, patch(
        "src.api.decomposition_routes._spawn_decomposition_cli",
        return_value=4242,
    ) as p_spawn:
        resp = client.post(
            f"/api/decomposition/runs/{rid}/rerun",
            json={"direction": "finer"},
        )
    assert resp.status_code == 201, resp.text
    p_metric.assert_called_once_with(direction="finer")
    p_event.assert_called_once()
    assert p_event.call_args.args[0] == "decomposition_rerun_triggered"
    # ISS-0024: the successor row is actually executed — spawn carries the
    # successor run_id and the ±1.5x direction, and the response exposes pid.
    p_spawn.assert_called_once()
    spawn_kwargs = p_spawn.call_args.kwargs
    assert spawn_kwargs["run_id"] == new_rid
    assert spawn_kwargs["rerun_direction"] == "finer"
    assert resp.json()["pid"] == 4242


def test_rerun_spawn_failure_is_logged_not_fatal(client):
    """ISS-0024: a spawn failure returns 201 with pid=null (row exists;
    the operator can re-trigger) — mirroring the trigger route's posture."""
    rid = uuid4()
    new_row = _run_row(run_id=uuid4())
    new_row["lineage_depth"] = 1
    new_row["direction"] = "coarser"
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._rerun_repository.create_rerun_run",
        return_value=new_row,
    ), patch(
        "src.api.decomposition_routes.record_decomposition_rerun"
    ), patch(
        "src.api.decomposition_routes._emit_elicitation_event"
    ), patch(
        "src.api.decomposition_routes._spawn_decomposition_cli",
        side_effect=OSError("spawn refused"),
    ):
        resp = client.post(
            f"/api/decomposition/runs/{rid}/rerun",
            json={"direction": "coarser"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["pid"] is None


def test_rerun_422_when_invalid_direction(client):
    resp = client.post(
        f"/api/decomposition/runs/{uuid4()}/rerun",
        json={"direction": "sideways"},
    )
    assert resp.status_code == 422


# ---------- 7. POST /runs/{run_id}/layer6/sample-cqs ----------


def test_sample_cqs_404_when_segment_not_in_layer4(client):
    rid = uuid4()
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid, layer4_hypotheses={"hypotheses": []}),
    ):
        resp = client.post(
            f"/api/decomposition/runs/{rid}/layer6/sample-cqs",
            json={"segment_name": "missing_segment", "document_excerpts": []},
        )
    assert resp.status_code == 404


def test_sample_cqs_happy_path_returns_snapshots(client):
    from src.decomposition.segmentation_map_models import GeneratedCQSnapshot

    rid = uuid4()
    payload = {
        "hypotheses": [
            {
                "name": "H1",
                "kind": "segmented",
                "segments": [
                    {
                        "name": "ops",
                        "description": "ops",
                        "representative_keywords": ["a"],
                        "representative_entities": ["b"],
                    }
                ],
            }
        ]
    }

    async def _stub(*args, **kwargs):
        return [
            GeneratedCQSnapshot(text="What is X?", cq_type="DESCRIPTIVE"),
            GeneratedCQSnapshot(text="What is Y?", cq_type="DESCRIPTIVE"),
        ]

    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid, layer4_hypotheses=payload),
    ), patch(
        "src.api.decomposition_routes._layer6_sample_cq_mod.generate_sample_cqs",
        side_effect=_stub,
    ):
        resp = client.post(
            f"/api/decomposition/runs/{rid}/layer6/sample-cqs",
            json={"segment_name": "ops", "document_excerpts": ["text"]},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["cqs"]) == 2
    assert body["cqs"][0]["text"] == "What is X?"


# ---------- 8. POST /runs/{run_id}/layer6/validation ----------


def test_layer6_validation_happy_path(client):
    rid = uuid4()
    out = _run_row(run_id=rid, layer6_validation={"segments": []})
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._run_repository.update_layer6_validation",
        return_value=out,
    ), patch(
        "src.api.decomposition_routes._emit_elicitation_event"
    ) as p_event:
        resp = client.post(
            f"/api/decomposition/runs/{rid}/layer6/validation",
            json={
                "segments": [],
                "validated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    assert resp.status_code == 200, resp.text
    p_event.assert_called_once()
    assert p_event.call_args.args[0] == "decomposition_layer6_validation_recorded"


# ---------- 9. POST /runs/{run_id}/segmentation-map/ratify ----------


def _segmentation_map_payload(run_id: UUID, *, accepted: bool = False) -> dict:
    return {
        "schema_version": "1.0",
        "decomposition_run_id": str(run_id),
        "produced_at": "2024-01-01T00:00:00+00:00",
        "archive_root_canonical_hash": "abc",
        "null_hypothesis_accepted": accepted,
        "segments": [
            {
                "name": "ops",
                "description": "Ops segment",
                "build_priority": "high",
                "expected_entity_types": ["Legal_Entity"],
            }
        ],
    }


def test_ratify_segmentation_map_422_when_run_id_mismatch(client):
    rid = uuid4()
    other = uuid4()
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ):
        resp = client.post(
            f"/api/decomposition/runs/{rid}/segmentation-map/ratify",
            json=_segmentation_map_payload(other),
        )
    assert resp.status_code == 422


def test_ratify_segmentation_map_happy_path_emits_telemetry(client):
    rid = uuid4()
    map_id = uuid4()
    repo_row = {
        "segmentation_map_id": map_id,
        "decomposition_run_id": rid,
        "schema_version": "1.0",
        "payload_hash": "h" * 64,
        "previous_hash": None,
        "created_at": datetime.now(timezone.utc),
        "created_by": None,
        "null_hypothesis_accepted": False,
    }
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._seg_map_repository.create_map",
        return_value=repo_row,
    ), patch(
        "src.api.decomposition_routes.record_decomposition_segmentation_map_ratified"
    ) as p_metric, patch(
        "src.api.decomposition_routes._emit_elicitation_event"
    ) as p_event:
        resp = client.post(
            f"/api/decomposition/runs/{rid}/segmentation-map/ratify",
            json=_segmentation_map_payload(rid),
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["segmentation_map_id"] == str(map_id)
    assert body["payload_hash"] == "h" * 64
    p_metric.assert_called_once_with(null_hypothesis_accepted=False)
    p_event.assert_called_once()
    assert p_event.call_args.args[0] == "segmentation_map_ratified"


# ---------- 10. GET segmentation maps + YAML rendering ----------


def test_list_segmentation_maps_default_json(client):
    rid = uuid4()
    rows = [
        {
            "segmentation_map_id": uuid4(),
            "decomposition_run_id": rid,
            "schema_version": "1.0",
            "payload_hash": "h1",
            "previous_hash": None,
            "created_at": datetime.now(timezone.utc),
            "created_by": None,
            "null_hypothesis_accepted": False,
            "payload": {"schema_version": "1.0"},
        }
    ]
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._seg_map_repository.chain_for_run",
        return_value=rows,
    ):
        resp = client.get(f"/api/decomposition/runs/{rid}/segmentation-maps")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    assert len(resp.json()["maps"]) == 1


def test_list_segmentation_maps_yaml_rendering(client):
    rid = uuid4()
    rows = [
        {
            "segmentation_map_id": uuid4(),
            "decomposition_run_id": rid,
            "schema_version": "1.0",
            "payload_hash": "h1",
            "previous_hash": None,
            "created_at": datetime.now(timezone.utc),
            "created_by": None,
            "null_hypothesis_accepted": False,
            "payload": {"schema_version": "1.0"},
        }
    ]
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._seg_map_repository.chain_for_run",
        return_value=rows,
    ):
        resp = client.get(
            f"/api/decomposition/runs/{rid}/segmentation-maps",
            headers={"Accept": "application/yaml"},
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/yaml")
    assert "schema_version" in resp.text


def test_get_segmentation_map_404_when_missing(client):
    rid = uuid4()
    map_id = uuid4()
    with patch(
        "src.api.decomposition_routes._run_repository.get_run",
        return_value=_run_row(run_id=rid),
    ), patch(
        "src.api.decomposition_routes._seg_map_repository.get_map_by_id",
        return_value=None,
    ):
        resp = client.get(
            f"/api/decomposition/runs/{rid}/segmentation-maps/{map_id}"
        )
    assert resp.status_code == 404
