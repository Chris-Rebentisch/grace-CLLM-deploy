"""CI guard: calibration route modules MUST NOT import calibration_updater (D246 mirror).

Chunk 49 (D394–D396). Research §4.1 / D246+D382.
"""

import ast
import os


def _get_imports(filepath: str) -> set[str]:
    """Extract all import module paths from a Python file."""
    with open(filepath) as f:
        tree = ast.parse(f.read())

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def test_calibration_routes_does_not_import_updater():
    """src/api/calibration_routes.py must not import calibration_updater."""
    filepath = os.path.join("src", "api", "calibration_routes.py")
    if not os.path.exists(filepath):
        # File not yet created — this test passes vacuously until CP6.
        return
    imports = _get_imports(filepath)
    for imp in imports:
        assert "calibration_updater" not in imp, (
            f"calibration_routes.py imports {imp} — violates D246 CLI-only invariant"
        )


def test_ontology_routes_does_not_import_updater():
    """src/api/ontology_routes.py must not import calibration_updater."""
    filepath = os.path.join("src", "api", "ontology_routes.py")
    assert os.path.exists(filepath)
    imports = _get_imports(filepath)
    for imp in imports:
        assert "calibration_updater" not in imp, (
            f"ontology_routes.py imports {imp} — violates D246 CLI-only invariant"
        )


def test_main_does_not_import_updater():
    """src/api/main.py must not import calibration_updater."""
    filepath = os.path.join("src", "api", "main.py")
    assert os.path.exists(filepath)
    imports = _get_imports(filepath)
    for imp in imports:
        assert "calibration_updater" not in imp, (
            f"main.py imports {imp} — violates D246 CLI-only invariant"
        )
