"""Tests for regeneration_routes (§9 of chunk-23-spec.md)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import regeneration_routes
from src.regeneration.regeneration_config import (
    RegenSettings,
    reset_regen_settings,
)
from src.regeneration.regeneration_models import (
    RegenerationResponse,
    ResponseMetadata,
)
from src.regeneration.regeneration_pipeline import (
    AssembleStageError,
    RegenerationPipeline,
    RetrievalStageError,
    SynthesizeStageError,
)


@pytest.fixture
def _app() -> FastAPI:
    regeneration_routes.reset_pipeline_singleton()
    reset_regen_settings()
    app = FastAPI()
    app.include_router(regeneration_routes.router)
    yield app
    regeneration_routes.reset_pipeline_singleton()
    reset_regen_settings()


def _install_fake_pipeline(regenerate: AsyncMock) -> RegenerationPipeline:
    fake = MagicMock(spec=RegenerationPipeline)
    fake.regenerate = regenerate
    regeneration_routes._pipeline = fake
    return fake


def _ok_response() -> RegenerationResponse:
    return RegenerationResponse(
        query="hi",
        response_text="hello",
        phase_state="none",
        response_metadata=ResponseMetadata(phase_style_applied="d"),
    )


def test_post_query_success(_app: FastAPI) -> None:
    _install_fake_pipeline(AsyncMock(return_value=_ok_response()))
    client = TestClient(_app)
    r = client.post(
        "/api/regeneration/query",
        json={"query_text": "hi", "phase_state": "none"},
    )
    assert r.status_code == 200
    body = RegenerationResponse.model_validate(r.json())
    assert body.response_text == "hello"


def test_post_query_invalid_phase_state(_app: FastAPI) -> None:
    client = TestClient(_app)
    r = client.post(
        "/api/regeneration/query",
        json={"query_text": "hi", "phase_state": "bogus"},
    )
    assert r.status_code == 422


def test_post_query_malformed_retrieval_query(_app: FastAPI) -> None:
    client = TestClient(_app)
    r = client.post(
        "/api/regeneration/query",
        json={
            "query_text": "hi",
            "retrieval_query": {"top_k": "not-an-int"},
        },
    )
    assert r.status_code == 422


def test_post_query_retrieval_failure_returns_503(_app: FastAPI) -> None:
    exc = RetrievalStageError(
        "arcade unavailable",
        stage_latencies={"retrieve": 12.0},
    )
    _install_fake_pipeline(AsyncMock(side_effect=exc))
    client = TestClient(_app)
    r = client.post(
        "/api/regeneration/query",
        json={"query_text": "hi", "phase_state": "none"},
    )
    assert r.status_code == 503
    body = r.json()
    assert body["stage"] == "retrieve"
    assert body["error_type"] == "RetrievalStageError"
    assert body["request_id"] is not None
    assert body["stage_latencies_ms"]["retrieve"] == 12.0


def test_post_query_synthesize_failure_returns_502(_app: FastAPI) -> None:
    exc = SynthesizeStageError(
        "ollama refused",
        stage_latencies={"retrieve": 11.0, "assemble": 2.0},
    )
    _install_fake_pipeline(AsyncMock(side_effect=exc))
    client = TestClient(_app)
    r = client.post(
        "/api/regeneration/query",
        json={"query_text": "hi", "phase_state": "none"},
    )
    assert r.status_code == 502
    body = r.json()
    assert body["stage"] == "synthesize"
    assert body["stage_latencies_ms"]["retrieve"] == 11.0
    assert body["stage_latencies_ms"]["assemble"] == 2.0


def test_post_query_assemble_failure_returns_500(_app: FastAPI) -> None:
    exc = AssembleStageError(
        "prompt assembly failed",
        stage_latencies={"retrieve": 10.0},
    )
    _install_fake_pipeline(AsyncMock(side_effect=exc))
    client = TestClient(_app)
    r = client.post(
        "/api/regeneration/query",
        json={"query_text": "hi", "phase_state": "none"},
    )
    assert r.status_code == 500
    body = r.json()
    assert body["stage"] == "assemble"


def test_get_config_returns_only_allowlist_fields(_app: FastAPI) -> None:
    client = TestClient(_app)
    r = client.get("/api/regeneration/config")
    assert r.status_code == 200
    body = r.json()
    expected_keys = {
        "system_budget_tokens",
        "context_budget_tokens",
        "query_budget_tokens",
        "response_budget_tokens",
        "total_input_budget_tokens",
        "regeneration_model",
        "regeneration_temperature",
        "chars_per_token",
        "enable_claim_span_detection",
        "span_detector_mode",
        "phase_style_overrides_applied",
    }
    assert set(body.keys()) == expected_keys
    # Ensure nothing like "debug_log_prompts" or raw template strings leaked
    assert "system_prompt_template" not in body
    assert "debug_log_prompts" not in body
    assert "phase_style_open" not in body
