"""Tests for enqueue_event transactional reuse (CP3, D446).

Verifies the ``db`` and ``session_id_override`` optional parameters added
to ``enqueue_event()`` in Chunk 65.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch
from uuid import UUID, uuid4

from src.elicitation.bridge import enqueue_event


class TestEnqueueEventReusesProvidedDb:
    """When ``db`` is provided, enqueue_event uses raw INSERT on that session."""

    def test_enqueue_event_reuses_provided_db(self):
        mock_db = MagicMock()

        enqueue_event(
            event_type="kill_switch_engaged",
            payload={"actor": "admin", "all_tiers_disabled": True},
            db=mock_db,
        )

        # db.execute should have been called (raw INSERT path, not write_event)
        mock_db.execute.assert_called_once()

    def test_enqueue_event_uses_session_id_override(self):
        """When ``session_id_override`` is provided, the raw INSERT row carries it."""
        override_id = uuid4()
        mock_db = MagicMock()

        enqueue_event(
            event_type="kill_switch_engaged",
            payload={"actor": "admin", "all_tiers_disabled": True},
            db=mock_db,
            session_id_override=override_id,
        )

        # Verify db.execute was called and the INSERT values contain our session_id
        mock_db.execute.assert_called_once()
        insert_call = mock_db.execute.call_args
        # The insert statement's compiled params should contain our override_id
        insert_stmt = insert_call[0][0]
        # Check the compiled parameters contain the session_id override
        compiled = insert_stmt.compile()
        params = compiled.params
        assert params["session_id"] == override_id
