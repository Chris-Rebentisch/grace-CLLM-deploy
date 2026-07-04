"""Tests for src.elicitation.bridge (Chunk 42 telemetry path)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from src.elicitation.bridge import enqueue_event


def test_enqueue_event_validates_and_writes_once():
    run_id = uuid4()
    fake_session = MagicMock()
    factory = MagicMock(return_value=fake_session)
    with patch(
        "src.elicitation.bridge.get_session_factory",
        return_value=factory,
    ), patch(
        "src.elicitation.bridge.write_event",
    ) as write_mock:
        enqueue_event(
            event_type="permission_matrix_hypothesis_generated",
            payload={
                "run_id": str(run_id),
                "cluster_count": 2,
                "has_null_hypothesis": True,
            },
        )
    write_mock.assert_called_once()
    env = write_mock.call_args[0][1]
    assert env.event_type == "permission_matrix_hypothesis_generated"
    assert env.payload["cluster_count"] == 2
    fake_session.close.assert_called_once()
