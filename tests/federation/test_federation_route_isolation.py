"""Route isolation CI guard for federation routes (Chunk 51 CP7).

Verifies that ``src/api/federation_routes.py`` only imports
``src.federation.service`` for stateful operations — never
``src.federation.registry`` or ``src.federation.namespace_federation``
directly.
"""

from __future__ import annotations

import ast


def _get_imports(filepath: str) -> set[str]:
    """Extract all import module strings from a Python file."""
    with open(filepath) as f:
        tree = ast.parse(f.read())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def test_routes_must_not_import_registry():
    """federation_routes.py MUST NOT import src.federation.registry."""
    imports = _get_imports("src/api/federation_routes.py")
    for imp in imports:
        assert "federation.registry" not in imp, (
            f"federation_routes.py imports {imp!r} — "
            "must delegate through src.federation.service instead"
        )


def test_routes_must_not_import_namespace_federation():
    """federation_routes.py MUST NOT import src.federation.namespace_federation."""
    imports = _get_imports("src/api/federation_routes.py")
    for imp in imports:
        assert "namespace_federation" not in imp, (
            f"federation_routes.py imports {imp!r} — "
            "must delegate through src.federation.service instead"
        )


def test_routes_may_import_service():
    """federation_routes.py MAY import src.federation.service."""
    imports = _get_imports("src/api/federation_routes.py")
    assert any("federation.service" in imp for imp in imports), (
        "federation_routes.py should import src.federation.service"
    )
