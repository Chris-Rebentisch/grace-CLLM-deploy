"""CI guard: proposal_routes.py must NOT import proposal_generator (D246 mirror, Chunk 47)."""

import ast
from pathlib import Path

import pytest

_ROUTE_FILE = Path(__file__).resolve().parent.parent.parent / "src" / "api" / "proposal_routes.py"


class TestProposalRouteIsolation:
    def test_no_import_of_proposal_generator(self):
        """src/api/proposal_routes.py must not import src.ontology.proposal_generator."""
        source = _ROUTE_FILE.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "proposal_generator" not in alias.name, (
                        f"proposal_routes.py imports {alias.name} — D246 violation"
                    )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "proposal_generator" not in module, (
                    f"proposal_routes.py imports from {module} — D246 violation"
                )

    def test_no_import_of_decomposition(self):
        """D246 mirror inheritance check."""
        source = _ROUTE_FILE.read_text()
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                assert "decomposition" not in module, (
                    f"proposal_routes.py imports from {module} — D246 violation"
                )
