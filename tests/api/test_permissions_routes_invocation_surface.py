"""Invocation-surface lint for permissions routes (Chunk 42, CP8).

Three architectural invariants enforced by static-string scan:

1. ``src/api/permissions_routes.py`` does NOT import
   ``src.permissions.hypothesis_generator`` or
   ``src.permissions.drift_detector`` (D246 mirror). Heavy work runs
   out-of-process via ``subprocess.Popen``.

2. The trigger routes use ``subprocess.Popen`` with
   ``start_new_session=True`` so the spawned CLI is detached from the
   FastAPI process group.

3. Both Chunk 42 read-only POSTs are registered in
   ``src.mcp_server.server.READONLY_ROUTES`` (D237 allowlist
   extension; CP8).
"""

from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ROUTES_FILE = REPO_ROOT / "src" / "api" / "permissions_routes.py"


def test_routes_file_does_not_import_hypothesis_generator():
    """``permissions_routes.py`` must not import the hypothesis generator."""
    text = ROUTES_FILE.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\s*(?:import\s+src\.permissions\.hypothesis_generator\b"
        r"|from\s+src\.permissions\.hypothesis_generator(\.|\s))",
        re.MULTILINE,
    )
    assert not pattern.search(text), (
        "permissions_routes.py must not import "
        "src.permissions.hypothesis_generator (D246 mirror)."
    )


def test_routes_file_does_not_import_drift_detector():
    """``permissions_routes.py`` must not import the drift detector."""
    text = ROUTES_FILE.read_text(encoding="utf-8")
    pattern = re.compile(
        r"^\s*(?:import\s+src\.permissions\.drift_detector\b"
        r"|from\s+src\.permissions\.drift_detector(\.|\s))",
        re.MULTILINE,
    )
    assert not pattern.search(text), (
        "permissions_routes.py must not import "
        "src.permissions.drift_detector (D246 mirror)."
    )


def test_trigger_routes_use_detached_subprocess_popen():
    """Trigger routes must spawn the CLI via ``subprocess.Popen`` with
    ``start_new_session=True`` so the child detaches from FastAPI."""
    text = ROUTES_FILE.read_text(encoding="utf-8")
    assert "subprocess.Popen" in text, (
        "Trigger routes must use subprocess.Popen to spawn the CLI."
    )
    assert "start_new_session=True" in text, (
        "subprocess.Popen call must include start_new_session=True."
    )
    # Argv must reference the permissions CLI module path.
    assert "src.permissions.cli" in text, (
        "Trigger argv must reference the CLI module 'src.permissions.cli'."
    )


def test_readonly_posts_in_allowlist():
    """Both Chunk 42 read-only POSTs belong to ``READONLY_ROUTES`` (D237)."""
    from src.mcp_server.server import READONLY_ROUTES

    assert (
        "POST",
        "/api/permissions/matrix/hypothesis/generate",
    ) in READONLY_ROUTES
    assert (
        "POST",
        "/api/permissions/drift/run",
    ) in READONLY_ROUTES
