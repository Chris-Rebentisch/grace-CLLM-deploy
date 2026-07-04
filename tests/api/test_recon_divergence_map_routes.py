"""Tests for the Cross-Executive Divergence Map route surface (Chunk 37, D284/D285).

Four tests:

1. Generate happy path → 201 with four-bucket response (self-comparison;
   default-admit per B1).
2. GET-latest happy + 404 (segment_id, reviewer_a, reviewer_b triple).
3. GET-by-id 404.
4. Cross-reviewer POST without admin key → 401 (D285 interim gate).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.graph.arcade_client import get_arcade_client
from src.shared.database import get_engine


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_arcade_client():
    """Replace the Arcade dependency with an async mock returning a small
    populated graph (so per-type instance counts are non-empty)."""
    fake = AsyncMock()
    fake.execute_sql = AsyncMock(
        return_value={
            "result": [
                {"type_name": "Company", "cnt": 12},
                {"type_name": "Person", "cnt": 5},
            ]
        }
    )
    app.dependency_overrides[get_arcade_client] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_arcade_client, None)


@pytest.fixture(autouse=True)
def _clean_divergence_tables():
    """Wipe Chunk 37 tables between tests; preserve Chunk 36 tables."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM recon_divergence_maps"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM recon_divergence_maps"))
        conn.commit()


@pytest.fixture(autouse=True)
def _ensure_admin_key_unset(monkeypatch):
    """All tests in this module assume the local-dev no-key posture
    unless the test itself flips it. Without this, a stale env var on
    the developer's shell could short-circuit the cross-reviewer 401
    test."""
    monkeypatch.delenv("GRACE_ADMIN_KEY", raising=False)


def _insert_ratified_version(
    reviewer: str,
    schema_json: dict | None = None,
    segment_id: str | None = None,
) -> UUID:
    """Insert one row into ``ontology_versions`` and return its id.

    Uses raw SQL so we don't have to thread the schema_store API in
    a test. ``schema_json`` defaults to a minimal flat schema with one
    entity type so OM4OV diff has something to chew on.
    """
    if schema_json is None:
        schema_json = {
            "entity_types": {"Company": {"properties": {}}},
            "relationships": {},
        }
    vid = uuid4()
    engine = get_engine()
    with engine.connect() as conn:
        # Compute version_number via max+1.
        row = conn.execute(
            text(
                "SELECT COALESCE(MAX(version_number), 0) + 1 AS n "
                "FROM ontology_versions"
            )
        ).first()
        next_n = int(row.n) if row else 1
        conn.execute(
            text(
                """
                INSERT INTO ontology_versions
                    (id, version_number, schema_json, schema_modules,
                     hash_chain, source, reviewer, segment_id, is_active,
                     created_at)
                VALUES
                    (:id, :n, CAST(:sj AS JSONB), CAST('{}' AS JSONB),
                     :hc, 'discovery', :rv, :seg, false, now())
                """
            ),
            {
                "id": str(vid),
                "n": next_n,
                "sj": _to_jsonb_str(schema_json),
                "hc": f"h_{vid.hex[:16]}",
                "rv": reviewer,
                "seg": segment_id,
            },
        )
        conn.commit()
    return vid


def _to_jsonb_str(payload: dict) -> str:
    import json as _json

    return _json.dumps(payload)


# ---------------------------------------------------------------------------
# 1. Generate happy path (self-comparison; default-admit).
# ---------------------------------------------------------------------------


def test_generate_self_comparison_returns_201_with_four_buckets(client):
    schema_a = {
        "entity_types": {
            "Company": {"properties": {}},
            "Person": {"properties": {}},
        },
        "relationships": {},
    }
    schema_b = {
        "entity_types": {
            "Company": {"properties": {}},
            "Trust": {"properties": {}},
        },
        "relationships": {},
    }
    va = _insert_ratified_version("alice", schema_a, segment_id="seg37a")
    vb = _insert_ratified_version("alice", schema_b, segment_id="seg37a")

    resp = client.post(
        "/api/recon/divergence-map/generate",
        json={
            "version_a_id": str(va),
            "version_b_id": str(vb),
            "segment_id": "seg37a",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["reviewer_a"] == "alice"
    assert body["reviewer_b"] == "alice"
    bucket_names = {b["bucket_name"] for b in body["buckets"]}
    assert bucket_names == {
        "additive_A",
        "additive_B",
        "contradictory",
        "consensus",
    }


# ---------------------------------------------------------------------------
# 2. GET-latest happy + 404.
# ---------------------------------------------------------------------------


def test_get_latest_returns_200_after_generate_and_404_for_unknown(client):
    schema = {
        "entity_types": {"Company": {"properties": {}}},
        "relationships": {},
    }
    va = _insert_ratified_version("bob", schema, segment_id="seg37b")
    vb = _insert_ratified_version("bob", schema, segment_id="seg37b")

    gen = client.post(
        "/api/recon/divergence-map/generate",
        json={
            "version_a_id": str(va),
            "version_b_id": str(vb),
            "segment_id": "seg37b",
        },
    )
    assert gen.status_code == 201

    got = client.get(
        "/api/recon/divergence-map/latest",
        params={"segment_id": "seg37b", "reviewer_a": "bob", "reviewer_b": "bob"},
    )
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["reviewer_a"] == "bob"

    miss = client.get(
        "/api/recon/divergence-map/latest",
        params={
            "segment_id": "no_such_segment",
            "reviewer_a": "bob",
            "reviewer_b": "bob",
        },
    )
    assert miss.status_code == 404


# ---------------------------------------------------------------------------
# 3. GET-by-id 404.
# ---------------------------------------------------------------------------


def test_get_by_id_returns_404_for_unknown(client):
    unknown = uuid4()
    resp = client.get(f"/api/recon/divergence-map/{unknown}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Cross-reviewer POST without admin key → 401 (D285).
# ---------------------------------------------------------------------------


def test_cross_reviewer_post_without_admin_key_returns_401(client):
    schema = {
        "entity_types": {"Company": {"properties": {}}},
        "relationships": {},
    }
    va = _insert_ratified_version("alice", schema, segment_id="seg37c")
    vb = _insert_ratified_version("bob", schema, segment_id="seg37c")

    resp = client.post(
        "/api/recon/divergence-map/generate",
        json={
            "version_a_id": str(va),
            "version_b_id": str(vb),
            "segment_id": "seg37c",
        },
    )
    assert resp.status_code == 401, resp.text
    assert "admin key" in resp.json().get("detail", "").lower()
