"""Chunk 28 D219 — live-subprocess smoke harness.

Validates that a fresh uvicorn subprocess dispatch matches the route
definitions. TestClient in-process dispatch does not always catch routing
regressions (see Chunk 27 session handoff meta-observation).

Chunk 31 extension: the harness exercises both the localhost-bypass path
(`GRACE_ADMIN_KEY` unset) and the keyed path (`GRACE_ADMIN_KEY` set; the
shell script presents `X-Admin-Key` on the POST). POST /api/retrieval/query
is in `READONLY_ROUTES` so it admits regardless of the key, but the keyed
path validates the explicit header flow end-to-end.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "smoke-live-server.sh"


def _scrubbed_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Return a shell env without GRACE_ADMIN_KEY, optionally overlaid."""
    env = {k: v for k, v in os.environ.items() if k != "GRACE_ADMIN_KEY"}
    if extra:
        env.update(extra)
    return env


@pytest.mark.smoke
def test_live_server_harness_all_paths():
    """The harness must exit 0 against all parametrized paths.

    Base ``PATHS`` in ``smoke-live-server.sh`` include (non-exhaustive):
      - /metrics/                (Chunk 25 regression anchor; mount subpath)
      - /metrics                 (Chunk 69 D458; explicit route, no trailing slash)
      - /api/graph/info          (existing route)
      - /api/graph/entities?limit=5  (new Chunk 28 D212 route)
      - /api/retrieval/query     (Chunk 14 route; POST)
      - /api/extraction/reconciliation  (Chunk 34 CP10; POST ``{}``)
    """
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"smoke harness failed (exit={result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    # Each path reports its status line; sanity-check metrics + graph + POST anchors.
    stdout = result.stdout
    assert "/metrics/" in stdout
    assert "GET /metrics:" in stdout  # bare /metrics (D458); distinct from GET /metrics/:
    assert "/api/graph/info" in stdout
    assert "/api/graph/entities" in stdout
    assert "/api/retrieval/query" in stdout
    assert "/api/extraction/reconciliation" in stdout


@pytest.mark.smoke
def test_live_server_harness_localhost_bypass_path():
    """GRACE_ADMIN_KEY unset → script exits 0 via localhost bypass (Chunk 31)."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        env=_scrubbed_env(),
    )
    assert result.returncode == 0, (
        f"smoke harness (bypass) failed (exit={result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


@pytest.mark.smoke
def test_live_server_harness_keyed_path():
    """GRACE_ADMIN_KEY set → script presents X-Admin-Key and exits 0 (Chunk 31)."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
        env=_scrubbed_env({"GRACE_ADMIN_KEY": "smoke-test-key-deadbeef"}),
    )
    assert result.returncode == 0, (
        f"smoke harness (keyed) failed (exit={result.returncode})\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
