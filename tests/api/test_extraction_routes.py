"""Tests for the Chunk 34 extraction API surface (D255, D256).

Covers ``POST /api/extraction/mine-sample`` (D255) and
``POST /api/extraction/reconciliation`` (D256). Heavy-IO dependencies
(``MINESampler``, ``ExtractionLLMClient``, ``ArcadeClient``,
``provenance.reconciliation_check``) are patched at the route-module
boundary so these tests run without Ollama / ArcadeDB / live extraction.
"""

from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def patched_extraction_deps():
    """Patch the three constructors used by the route module."""
    with patch(
        "src.api.extraction_routes._build_arcade_client",
        return_value=MagicMock(),
    ) as p_arcade, patch(
        "src.api.extraction_routes._build_extraction_client",
        return_value=MagicMock(),
    ) as p_llm:
        yield {"arcade": p_arcade, "llm": p_llm}


# --- MINE sample ----------------------------------------------------------


@pytest.fixture()
def patched_mine_sampler():
    """Patch ``MINESampler`` so ``sample_document`` returns a fixed dict."""
    sample_id = uuid4()

    async def _fake_sample_document(document_id, session, client, arcade_client):
        return {
            "id": sample_id,
            "retention_score": 0.91,
            "total_facts": 11,
            "recovered_facts": 10,
            "judgments": [{"fact": "f1", "recovered": True}],
            "cached": False,
        }

    sampler_instance = MagicMock()
    sampler_instance.sample_document = AsyncMock(side_effect=_fake_sample_document)
    sampler_instance._schema_version_id = None
    with patch(
        "src.api.extraction_routes._build_mine_sampler",
        return_value=sampler_instance,
    ):
        yield {"sample_id": sample_id, "sampler": sampler_instance}


def test_mine_sample_happy_path(client, patched_extraction_deps, patched_mine_sampler):
    document_id = str(uuid4())
    with patch("src.api.extraction_routes.set_mine_retention_observation") as p_emit:
        resp = client.post(
            "/api/extraction/mine-sample",
            json={"document_id": document_id, "ontology_module": "core"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["retention_score"] == pytest.approx(0.91)
    assert body["total_facts"] == 11
    assert body["recovered_facts"] == 10
    assert body["mine_sample_id"] == str(patched_mine_sampler["sample_id"])
    assert body["judgments"] == [{"fact": "f1", "recovered": True}]

    assert p_emit.call_count == 1
    kw = p_emit.call_args.kwargs
    assert kw["ontology_module"] == "core"
    assert kw["schema_version_id"] == "unknown"
    assert kw["retention_ratio"] == pytest.approx(0.91)


def test_mine_sample_idempotent_returns_same_id(
    client, patched_extraction_deps, patched_mine_sampler
):
    """Re-calling with the same ``document_id`` returns the same ``mine_sample_id``.

    The dedup is owned by ``MINESampler.sample_document``; the route is a
    pass-through. We assert pass-through here.
    """
    document_id = str(uuid4())
    r1 = client.post("/api/extraction/mine-sample", json={"document_id": document_id})
    r2 = client.post("/api/extraction/mine-sample", json={"document_id": document_id})
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["mine_sample_id"] == r2.json()["mine_sample_id"]


def test_mine_sample_timeout_returns_504(client, patched_extraction_deps):
    """When ``asyncio.wait_for`` raises TimeoutError, the route returns 504."""

    async def _hang(document_id, session, client_, arcade_client_):
        await asyncio.sleep(10)
        raise AssertionError("should not reach")

    sampler_instance = MagicMock()
    sampler_instance.sample_document = AsyncMock(side_effect=_hang)
    with patch(
        "src.api.extraction_routes._build_mine_sampler",
        return_value=sampler_instance,
    ), patch(
        "src.api.extraction_routes._load_mine_timeout_seconds", return_value=1
    ), patch(
        "src.api.extraction_routes.asyncio.wait_for",
        AsyncMock(side_effect=asyncio.TimeoutError()),
    ):
        resp = client.post(
            "/api/extraction/mine-sample",
            json={"document_id": str(uuid4())},
        )
    assert resp.status_code == 504, resp.text
    assert resp.json()["detail"] == "MINE sampling timed out"


def test_mine_sample_keyed_admission_requires_admin_key(monkeypatch):
    """When ``GRACE_ADMIN_KEY`` is set, a request without the X-Admin-Key header
    returns 401 (Chunk 31 default-deny) — the localhost bypass only fires when
    the key is unset.

    Patches the module-level ``GRACE_ADMIN_KEY`` directly (the middleware reads
    it at request time) instead of del+reimporting ``src.api.main`` /
    ``src.api.auth_middleware``. That del+reimport diverged sys.modules from the
    ``app`` refs other test files cached at collection, silently breaking later
    cross-file ``patch()``-es of GRACE_ADMIN_KEY (the proposal_create leak).
    """
    monkeypatch.setattr("src.api.auth_middleware.GRACE_ADMIN_KEY", "key-for-test")
    resp = TestClient(app).post(
        "/api/extraction/mine-sample",
        json={"document_id": str(uuid4())},
    )
    assert resp.status_code == 401, resp.text


# --- Reconciliation -------------------------------------------------------


def test_reconciliation_happy_path(client, patched_extraction_deps):
    fake_result = {"promoted": 3, "warnings": 1, "checked": 5}
    with patch(
        "src.api.extraction_routes.provenance.reconciliation_check",
        AsyncMock(return_value=fake_result),
    ):
        resp = client.post("/api/extraction/reconciliation", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == fake_result


def test_reconciliation_payload_parity_with_cli(client, patched_extraction_deps):
    """API and CLI must surface the same ``{promoted, warnings, checked}`` shape.

    Both call ``provenance.reconciliation_check`` directly. The CLI is
    tested separately for argparse/exit semantics; here we assert the API
    payload shape matches the CLI's documented output keys.
    """
    fake_result = {"promoted": 7, "warnings": 0, "checked": 9}
    with patch(
        "src.api.extraction_routes.provenance.reconciliation_check",
        AsyncMock(return_value=fake_result),
    ):
        api_resp = client.post("/api/extraction/reconciliation", json={})
    assert api_resp.status_code == 200
    api_keys = set(api_resp.json().keys())

    from src.extraction import reconciliation as cli_module

    assert api_keys == set(cli_module.RESPONSE_KEYS)


def test_reconciliation_keyed_admission_requires_admin_key(monkeypatch):
    """Same posture assertion as the mine-sample variant (module-var patch, no reload)."""
    monkeypatch.setattr("src.api.auth_middleware.GRACE_ADMIN_KEY", "key-for-test")
    resp = TestClient(app).post("/api/extraction/reconciliation", json={})
    assert resp.status_code == 401, resp.text
