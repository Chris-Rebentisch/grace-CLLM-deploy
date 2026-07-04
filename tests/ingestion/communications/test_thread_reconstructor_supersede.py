"""F-0032(a) / ISS-0036: supersession re-drive path on the thread reconstructor.

In the documented pipeline order (thread reconstruction FIRST, then extraction
bridge) the graph is EMPTY when the reconstructor's same-invocation
supersession fires, and re-running `run` no-ops (`no_events_to_process`).
The `supersede` subcommand re-applies supersession over ALREADY-threaded
events without requiring new events.

Also covers the F-0049-family metrics-init call at CLI main entry.

Pure unit tests — mock session, no DB, no services.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.ingestion.communications.thread_reconstructor import (
    _build_argparser,
    main,
    resupersede_threads,
)


def _event_row(eid, message_id, thread_id, position, sent_at=None):
    return SimpleNamespace(
        id=eid,
        message_id=message_id,
        thread_id=thread_id,
        thread_position=position,
        sent_at=sent_at or datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []

    def execute(self, stmt, params=None):
        self.queries.append(str(stmt))
        return _FakeResult(self._rows)


# --- argparser contract -----------------------------------------------------


def test_argparser_accepts_supersede_subcommand():
    parser = _build_argparser()
    args = parser.parse_args(
        ["supersede", "--source-id", "abc", "--thread-id", "<t@x>", "--limit", "5", "--dry-run"]
    )
    assert args.command == "supersede"
    assert args.thread_id == "<t@x>"
    assert args.limit == 5
    assert args.dry_run is True


def test_argparser_run_subcommand_unchanged():
    """The route-spawned `run` argv contract must be untouched (D476)."""
    parser = _build_argparser()
    args = parser.parse_args(["run", "--source-id", "abc", "--limit", "10", "--dry-run", "--reprocess"])
    assert args.command == "run"
    assert args.reprocess is True


# --- resupersede_threads ----------------------------------------------------


def test_resupersede_groups_threaded_events_and_applies_supersession():
    rows = [
        _event_row(1, "<m0@x>", "<m0@x>", 0),
        _event_row(2, "<m1@x>", "<m0@x>", 1),
        _event_row(3, "<n0@x>", "<n0@x>", 0),
    ]
    session = _FakeSession(rows)

    with patch(
        "src.ingestion.communications.thread_reconstructor._apply_supersession_for_threads",
        return_value={"threads_processed": 2, "vertex_superseded": 1, "claims_superseded": 1},
    ) as mock_apply:
        result = resupersede_threads(session)

    assert result["thread_count"] == 2
    assert result["event_count"] == 3
    assert result["claims_superseded"] == 1

    mock_apply.assert_called_once()
    (_, thread_members), _ = mock_apply.call_args
    assert set(thread_members) == {"<m0@x>", "<n0@x>"}
    assert [m["message_id"] for m in thread_members["<m0@x>"]] == ["<m0@x>", "<m1@x>"]
    assert thread_members["<m0@x>"][1]["thread_position"] == 1

    # Re-drive must select ALREADY-threaded events (the whole point of F-0032a).
    assert "thread_id IS NOT NULL" in session.queries[0]


def test_resupersede_dry_run_reports_without_applying():
    rows = [_event_row(1, "<m0@x>", "<m0@x>", 0), _event_row(2, "<m1@x>", "<m0@x>", 1)]
    session = _FakeSession(rows)

    with patch(
        "src.ingestion.communications.thread_reconstructor._apply_supersession_for_threads"
    ) as mock_apply:
        result = resupersede_threads(session, dry_run=True)

    assert result == {"thread_count": 1, "event_count": 2}
    mock_apply.assert_not_called()


def test_resupersede_no_threaded_events_is_noop():
    session = _FakeSession([])
    result = resupersede_threads(session)
    assert result == {"thread_count": 0, "event_count": 0}


def test_resupersede_thread_id_filter_reaches_query():
    session = _FakeSession([])
    resupersede_threads(session, thread_id="<only@x>")
    assert "thread_id = :thread_id" in session.queries[0]


# --- main() dispatch + metrics init ------------------------------------------


def test_main_supersede_dispatch_and_metrics_init():
    """`supersede` argv dispatches to resupersede_threads, and main() calls
    init_subprocess_metrics (F-0049 family — CLI counters are otherwise
    structurally unobservable)."""
    with (
        patch.object(sys, "argv", ["thread_reconstructor", "supersede", "--dry-run"]),
        patch(
            "src.analytics.subprocess_metrics.init_subprocess_metrics"
        ) as mock_metrics,
        patch(
            "src.ingestion.communications.thread_reconstructor.get_engine",
            return_value=MagicMock(),
        ),
        patch(
            "src.ingestion.communications.thread_reconstructor.resupersede_threads",
            return_value={"thread_count": 0, "event_count": 0},
        ) as mock_resupersede,
    ):
        main()

    mock_metrics.assert_called_once()
    mock_resupersede.assert_called_once()
    assert mock_resupersede.call_args.kwargs["dry_run"] is True
    assert mock_resupersede.call_args.kwargs["thread_id"] is None


def test_main_run_dispatch_still_reconstructs():
    with (
        patch.object(sys, "argv", ["thread_reconstructor", "run", "--dry-run"]),
        patch("src.analytics.subprocess_metrics.init_subprocess_metrics"),
        patch(
            "src.ingestion.communications.thread_reconstructor.get_engine",
            return_value=MagicMock(),
        ),
        patch(
            "src.ingestion.communications.thread_reconstructor.reconstruct_threads",
            return_value={"thread_count": 0, "event_count": 0},
        ) as mock_reconstruct,
    ):
        main()

    mock_reconstruct.assert_called_once()
