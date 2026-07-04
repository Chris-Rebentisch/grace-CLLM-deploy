"""Route-isolation CI guard for connector routes (CP8, D246 mirror).

Ensures ``src/api/connectors_routes.py`` does NOT import
``src.connectors.sync_pipeline`` — the sync trigger route must spawn
the CLI via ``subprocess.Popen([..., start_new_session=True])``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROUTES_FILE = Path(__file__).resolve().parents[2] / "src" / "api" / "connectors_routes.py"


def test_ast_no_sync_pipeline_import() -> None:
    """AST verification: connectors_routes.py does NOT import sync_pipeline."""
    source = ROUTES_FILE.read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and "sync_pipeline" in node.module:
                pytest.fail(
                    f"connectors_routes.py imports sync_pipeline at line {node.lineno} "
                    f"— D246 violation"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "sync_pipeline" in alias.name:
                    pytest.fail(
                        f"connectors_routes.py imports sync_pipeline at line {node.lineno} "
                        f"— D246 violation"
                    )


def test_runtime_no_sync_pipeline_import() -> None:
    """Runtime import check: importing connectors_routes does not pull in sync_pipeline."""
    import importlib
    import sys

    # Remove sync_pipeline from sys.modules if loaded
    sys.modules.pop("src.connectors.sync_pipeline", None)

    # Import the routes module
    importlib.import_module("src.api.connectors_routes")

    # sync_pipeline should NOT be in sys.modules
    assert "src.connectors.sync_pipeline" not in sys.modules, (
        "Importing connectors_routes.py loaded sync_pipeline — D246 violation"
    )


def test_readonly_routes_includes_connector_gets() -> None:
    """READONLY_ROUTES includes the three connector GET tuples."""
    from src.mcp_server.server import READONLY_ROUTES

    expected = {
        ("GET", "/api/connectors"),
        ("GET", "/api/connectors/{connector_type}/health"),
        ("GET", "/api/connectors/{connector_type}/sync/status"),
    }
    assert expected.issubset(READONLY_ROUTES), (
        f"Missing connector GET routes in READONLY_ROUTES: {expected - READONLY_ROUTES}"
    )
