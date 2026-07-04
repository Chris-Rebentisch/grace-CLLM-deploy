"""Tests for /api/regeneration/close-summary + /close-confirm (Chunk 27, CP10)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from src.api.main import app
    from src.api.session_routes import _reset_session_records_for_tests

    _reset_session_records_for_tests()
    return TestClient(app)


class _FakeLLMResponse:
    def __init__(self, text: str) -> None:
        self.text = text


def _close_summary_body(**overrides):
    base = {
        "session_id": str(uuid4()),
        "phase_state": "close",
        "messages": [
            {
                "role": "user",
                "content": "Summarize our discussion about Acme.",
                "claim_spans": None,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
            {
                "role": "assistant",
                "content": "Acme is a family trust.",
                "claim_spans": None,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            },
        ],
        "phase_durations_ms": {"open": 60_000},
    }
    base.update(overrides)
    return base


def test_close_summary_happy_path_uses_chunk23_primitive_with_custom_template(client):
    fake = AsyncMock(return_value=_FakeLLMResponse("Narrative summary text."))
    with patch(
        "src.api.session_routes.ResponseSynthesizer.synthesize",
        new=fake,
    ):
        resp = client.post("/api/regeneration/close-summary", json=_close_summary_body())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["summary"]["narrative"] == "Narrative summary text."
    # CP27-only: structured slots are empty placeholders.
    assert data["summary"]["decisions_recorded"] == []
    assert data["summary"]["cqs_flipped_state"] == []
    # The synthesizer was invoked with the close-summary template as the
    # system prompt (D193 lock: custom prompt template only).
    assert fake.call_count == 1
    assembled_arg = fake.call_args.args[0]
    assert (
        "narrative summary" in assembled_arg.system_prompt.lower()
        or "completed a knowledge-graph session"
        in assembled_arg.system_prompt.lower()
    )


def test_close_summary_rejects_non_close_phase_state(client):
    body = _close_summary_body(phase_state="open")
    resp = client.post("/api/regeneration/close-summary", json=body)
    # Pydantic rejects the Literal mismatch with 422 — either is an
    # acceptable client-side error.
    assert resp.status_code in (400, 422)


def test_close_confirm_records_session_and_rejects_double_close(client):
    session_id = str(uuid4())
    body = {
        "session_id": session_id,
        "final_summary": {
            "narrative": "Final narrative.",
            "ontology_changes": [],
            "cqs_flipped_state": [],
            "decisions_recorded": [],
            "deferred_items": [],
            "certainty_band_shifts": [],
        },
        "summary_edited": True,
        "summary_rejected": False,
    }
    resp = client.post("/api/regeneration/close-confirm", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == session_id
    assert data["session_status"] == "closed"

    # Double-close is a 409 conflict.
    resp2 = client.post("/api/regeneration/close-confirm", json=body)
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["error_type"] == "session_already_closed"
