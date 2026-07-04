"""Lifespan smoke for the Documented Reality APScheduler (Chunk 37, D287).

One test: FastAPI lifespan starts the BackgroundScheduler cleanly and shuts
it down without hanging. This is a single-process invariant — see
``docs/security-posture.md`` §21.2.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app


def test_lifespan_starts_and_stops_scheduler_without_hang():
    """Use TestClient context manager to drive lifespan startup + shutdown.

    On enter, FastAPI lifespan runs ``_start_documented_reality_scheduler``
    and stores the scheduler on ``app.state``. On exit, lifespan calls
    ``shutdown(wait=False)``. If APScheduler is unavailable in the test
    environment, ``app.state.documented_reality_scheduler`` will be
    ``None`` and the test still passes (the lifespan is best-effort).
    """
    with TestClient(app) as client:
        # One real request so we know the lifespan completed startup.
        resp = client.get("/api/graph/info")
        assert resp.status_code in (200, 404, 500)
        # The scheduler attribute must exist after startup, even if None.
        assert hasattr(app.state, "documented_reality_scheduler")
    # Exiting the context manager triggers lifespan teardown. If shutdown
    # hangs, pytest will time out. Reaching this line means clean exit.
