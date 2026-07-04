"""Invocation-surface lint for decomposition routes (Chunk 41, CP8/CP11).

Three architectural invariants enforced by static-string scan:

1. ``src/api/decomposition_routes.py`` does NOT import
   ``src.decomposition.pipeline.orchestrator`` (D246/D315 mirror).
   Heavy work runs out-of-process via ``subprocess.Popen``.

2. The trigger route uses ``subprocess.Popen`` with
   ``start_new_session=True`` so the spawned CLI is detached from
   the FastAPI process group.

3. The Layer 6 sample-CQ POST is registered in
   ``src.mcp_server.server.READONLY_ROUTES`` (D237 allowlist
   extension; D328 spec §7.5).
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTES_FILE = REPO_ROOT / "src" / "api" / "decomposition_routes.py"


def test_routes_file_does_not_import_orchestrator():
    """``decomposition_routes.py`` must not import the pipeline orchestrator."""
    text = ROUTES_FILE.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\s*(?:import\s+src\.decomposition\.pipeline\.orchestrator\b"
        r"|from\s+src\.decomposition\.pipeline\.orchestrator(\.|\s))",
        re.MULTILINE,
    )
    assert not pattern.search(text), (
        "decomposition_routes.py must not import "
        "src.decomposition.pipeline.orchestrator (D246/D315)."
    )


def test_trigger_route_uses_detached_subprocess_popen():
    """Trigger route must spawn the CLI via ``subprocess.Popen`` with
    ``start_new_session=True`` so the child detaches from FastAPI
    (uvicorn reload would otherwise terminate the pipeline)."""
    text = ROUTES_FILE.read_text(encoding="utf-8")
    assert "subprocess.Popen" in text, (
        "Trigger route must use subprocess.Popen to spawn the CLI."
    )
    assert "start_new_session=True" in text, (
        "subprocess.Popen call must include start_new_session=True."
    )
    # Argv must reference the CLI module path.
    assert "src.decomposition.pipeline" in text, (
        "Trigger argv must reference the CLI module 'src.decomposition.pipeline'."
    )


def test_sample_cq_post_in_readonly_allowlist():
    """The Layer 6 sample-CQ POST belongs to ``READONLY_ROUTES`` (D237)."""
    from src.mcp_server.server import READONLY_ROUTES

    assert (
        "POST",
        "/api/decomposition/runs/{run_id}/layer6/sample-cqs",
    ) in READONLY_ROUTES
