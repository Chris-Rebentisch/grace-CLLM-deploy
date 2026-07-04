"""CI guard: proposal_routes.py imports change_executor (D393) and NOT proposal_generator (D389).

Chunk 48, CP4. Both conditions must hold.
"""

import ast
from pathlib import Path


_ROUTES_PATH = Path(__file__).resolve().parents[2] / "src" / "api" / "proposal_routes.py"


def _get_imports(path: Path) -> set[str]:
    """Return the set of dotted module names imported by *path*."""
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class TestRouteIsolation:
    def test_change_executor_import_present(self) -> None:
        """D393 scoped exception: proposal_routes.py MUST import change_executor."""
        imports = _get_imports(_ROUTES_PATH)
        assert any("change_executor" in m for m in imports), (
            "proposal_routes.py must import src.ontology.change_executor (D393 scoped exception)"
        )

    def test_proposal_generator_import_absent(self) -> None:
        """D389 invariant: proposal_routes.py MUST NOT import proposal_generator."""
        imports = _get_imports(_ROUTES_PATH)
        assert not any("proposal_generator" in m for m in imports), (
            "proposal_routes.py must NOT import src.ontology.proposal_generator (D389 invariant)"
        )
