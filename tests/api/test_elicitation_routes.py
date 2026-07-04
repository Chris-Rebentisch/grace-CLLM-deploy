"""Tests for POST /api/elicitation/events (Chunk 27, D202)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.shared.database import get_session_factory


@pytest.fixture()
def client():
    from src.api.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_elicitation_events():
    factory = get_session_factory()
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE elicitation_events DISABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.execute(text("DELETE FROM elicitation_events"))
        db.execute(
            text(
                "ALTER TABLE elicitation_events ENABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.commit()
    yield
    with factory() as db:
        db.execute(
            text(
                "ALTER TABLE elicitation_events DISABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.execute(text("DELETE FROM elicitation_events"))
        db.execute(
            text(
                "ALTER TABLE elicitation_events ENABLE TRIGGER "
                "trig_elicitation_events_immutable"
            )
        )
        db.commit()


def _base_envelope(**overrides):
    base = {
        "event_id": str(uuid4()),
        "event_type": "session_started",
        "session_id": str(uuid4()),
        "actor_type": "human",
        "phase_name": "open",
        "emitted_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": 1,
        "grace_version": "0.27.0",
        "payload": {
            "plan_id": None,
            "instrument_selected": None,
            "rationale_string": None,
        },
        "payload_schema_version": 1,
    }
    base.update(overrides)
    return base


def test_post_event_accepts_valid_envelope_and_persists(client):
    envelope = _base_envelope()
    resp = client.post("/api/elicitation/events", json=envelope)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["event_id"] == envelope["event_id"]
    assert "accepted_at" in body


def test_post_event_rejects_missing_required_field_with_422(client):
    envelope = _base_envelope()
    envelope.pop("grace_version")
    resp = client.post("/api/elicitation/events", json=envelope)
    assert resp.status_code == 422, resp.text


def test_post_event_rejects_mismatched_payload_with_telemetry_validation_error(client):
    # session_resumed payload missing required resumed_to_phase.
    envelope = _base_envelope(
        event_type="session_resumed",
        payload={
            "resumed_at": datetime.now(timezone.utc).isoformat(),
            "paused_duration_ms": 100,
        },
    )
    resp = client.post("/api/elicitation/events", json=envelope)
    assert resp.status_code == 422
    body = resp.json()
    assert body["detail"]["error_type"] == "telemetry_validation_error"


def test_append_only_trigger_blocks_update_and_delete(client):
    envelope = _base_envelope()
    resp = client.post("/api/elicitation/events", json=envelope)
    assert resp.status_code == 201
    factory = get_session_factory()
    with factory() as db:
        with pytest.raises(Exception):
            db.execute(
                text(
                    "UPDATE elicitation_events SET event_type='hacked'"
                )
            )
            db.commit()
    # Connection may be in a failed-transaction state; reset.
    with factory() as db:
        with pytest.raises(Exception):
            db.execute(text("DELETE FROM elicitation_events"))
            db.commit()
