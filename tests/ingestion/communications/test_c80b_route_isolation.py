"""CP6 — Route isolation test (D246 mirror, Chunk 80b).

Asserts src/api/ingestion_routes.py does not import corroboration_scorer
or bootstrap_pipe.
"""

import ast
from pathlib import Path


def test_ingestion_routes_does_not_import_c80b_modules():
    """D246 route isolation: ingestion_routes.py must not import corroboration_scorer or bootstrap_pipe."""
    route_file = Path(__file__).resolve().parents[3] / "src" / "api" / "ingestion_routes.py"
    source = route_file.read_text()
    tree = ast.parse(source)

    forbidden = {"corroboration_scorer", "bootstrap_pipe"}
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for f in forbidden:
                    if f in alias.name:
                        violations.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for f in forbidden:
                if f in module:
                    violations.append(f"from {module} import ...")

    assert not violations, (
        f"D246 violation: ingestion_routes.py imports forbidden modules: {violations}"
    )
