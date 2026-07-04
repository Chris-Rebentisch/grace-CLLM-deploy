"""CI guard: src/api/ingestion_routes.py MUST NOT import src.extraction.extraction_bridge (D246 mirror, D508)."""

import ast
from pathlib import Path


def test_ingestion_routes_does_not_import_extraction_bridge():
    """Ensure D246 route isolation: ingestion_routes.py must not import extraction_bridge."""
    source = Path("src/api/ingestion_routes.py").read_text()
    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if "extraction_bridge" in module:
                raise AssertionError(
                    f"D246 violation: ingestion_routes.py imports {module} "
                    f"(line {node.lineno}). extraction_bridge must be spawned "
                    f"via subprocess.Popen, not imported."
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if "extraction_bridge" in alias.name:
                    raise AssertionError(
                        f"D246 violation: ingestion_routes.py imports {alias.name} "
                        f"(line {node.lineno})."
                    )
