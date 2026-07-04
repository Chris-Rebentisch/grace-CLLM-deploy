"""Route-isolation CI guard for agent daemon (Chunk 50, D246/D398).

daemon_routes.py MUST NOT import agent_daemon (D246 mirror).
daemon_routes.py MAY import change_executor.apply_proposal (D393 exception).
ontology_routes.py, proposal_routes.py, calibration_routes.py MUST NOT import agent_daemon.
"""

from __future__ import annotations

import ast
from pathlib import Path


_SRC_API = Path(__file__).resolve().parents[2] / "src" / "api"


def _imports_in_file(filepath: Path) -> set[str]:
    """Return the set of imported module strings from a Python file."""
    source = filepath.read_text()
    tree = ast.parse(source)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def test_daemon_routes_does_not_import_agent_daemon():
    """daemon_routes.py MUST NOT import agent_daemon."""
    imports = _imports_in_file(_SRC_API / "daemon_routes.py")
    forbidden = [i for i in imports if "agent_daemon" in i]
    assert not forbidden, f"daemon_routes.py imports agent_daemon: {forbidden}"


def test_daemon_routes_may_import_change_executor():
    """daemon_routes.py MAY import change_executor (D393 exception)."""
    source = (_SRC_API / "daemon_routes.py").read_text()
    assert "change_executor" in source


def test_ontology_routes_does_not_import_agent_daemon():
    """ontology_routes.py MUST NOT import agent_daemon."""
    imports = _imports_in_file(_SRC_API / "ontology_routes.py")
    forbidden = [i for i in imports if "agent_daemon" in i]
    assert not forbidden, f"ontology_routes.py imports agent_daemon: {forbidden}"


def test_proposal_routes_does_not_import_agent_daemon():
    """proposal_routes.py MUST NOT import agent_daemon."""
    imports = _imports_in_file(_SRC_API / "proposal_routes.py")
    forbidden = [i for i in imports if "agent_daemon" in i]
    assert not forbidden, f"proposal_routes.py imports agent_daemon: {forbidden}"
