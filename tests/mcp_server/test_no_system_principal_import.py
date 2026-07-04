"""CP1 — R12 enforcement: no SystemPrincipal / SYSTEM_PRINCIPAL imports
in any src/mcp_server/*.py file.

AST-level grep so f-string / comment false positives are impossible.
The D346 lint script (check-cypher-principal-context.sh) covers src/api/*
only; this test is the MCP-specific guard.
"""

from __future__ import annotations

import ast
import pathlib


_MCP_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "mcp_server"

_FORBIDDEN_NAMES = {"SystemPrincipal", "SYSTEM_PRINCIPAL"}


def test_no_system_principal_import_in_mcp_server():
    """No src/mcp_server/*.py file imports SystemPrincipal or SYSTEM_PRINCIPAL."""
    violations: list[str] = []
    for py_file in sorted(_MCP_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name in _FORBIDDEN_NAMES:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports {name}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name
                    if name in _FORBIDDEN_NAMES:
                        violations.append(
                            f"{py_file.name}:{node.lineno} imports {name}"
                        )
    assert not violations, (
        f"R12 violation — SystemPrincipal imported in src/mcp_server/: "
        f"{violations}"
    )


def test_no_system_principal_name_reference_in_mcp_server():
    """No src/mcp_server/*.py file references SystemPrincipal as a Name node."""
    violations: list[str] = []
    for py_file in sorted(_MCP_DIR.glob("*.py")):
        tree = ast.parse(py_file.read_text(), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
                violations.append(
                    f"{py_file.name}:{node.lineno} references {node.id}"
                )
    assert not violations, (
        f"R12 violation — SystemPrincipal referenced in src/mcp_server/: "
        f"{violations}"
    )
