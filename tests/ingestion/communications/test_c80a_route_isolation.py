"""CI guard: src/api/ingestion_routes.py MUST NOT import thread_reconstructor or supersession (D246 mirror, D513)."""

import ast
from pathlib import Path


def test_ingestion_routes_does_not_import_thread_reconstructor_or_supersession():
    """Ensure D246 route isolation: ingestion_routes.py must not import thread_reconstructor or supersession."""
    source = Path("src/api/ingestion_routes.py").read_text()
    tree = ast.parse(source)

    forbidden = {"thread_reconstructor", "supersession"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for name in forbidden:
                if name in module:
                    raise AssertionError(
                        f"D246 violation: ingestion_routes.py imports {module} "
                        f"(line {node.lineno}). {name} must be spawned "
                        f"via subprocess.Popen, not imported."
                    )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                for name in forbidden:
                    if name in alias.name:
                        raise AssertionError(
                            f"D246 violation: ingestion_routes.py imports {alias.name} "
                            f"(line {node.lineno})."
                        )
