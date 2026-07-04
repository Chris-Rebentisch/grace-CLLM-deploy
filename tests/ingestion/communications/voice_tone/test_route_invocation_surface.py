"""Import-guard tests for Voice & Tone modules (Chunk 58, CP7).

Validates:
1. D246 mirror: communications_routes.py does NOT import profile_generator
2. Lock-R2: recipient_classifier.py does NOT import src.graph.*
3. Lock-R3: role_resolver.py IS the sole voice_tone module importing src.graph.arcade_client
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_VOICE_TONE_DIR = Path("src/ingestion/communications/voice_tone")
_ROUTES_FILE = Path("src/api/communications_routes.py")


def _get_imports(filepath: Path) -> list[str]:
    """Extract all import targets from a Python file's AST."""
    source = filepath.read_text()
    tree = ast.parse(source, filename=str(filepath))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)
    return imports


class TestD246Mirror:
    """D246 mirror: route module must not import profile_generator."""

    def test_routes_does_not_import_profile_generator(self):
        """src/api/communications_routes.py must not import profile_generator."""
        assert _ROUTES_FILE.exists(), f"{_ROUTES_FILE} not found"
        source = _ROUTES_FILE.read_text()
        imports = _get_imports(_ROUTES_FILE)

        # AST-level check is authoritative — catches both top-level and
        # lazy (inside-function) imports. Docstrings and comments are excluded
        # because AST only parses actual import statements.
        for imp in imports:
            assert "profile_generator" not in imp, (
                f"D246 mirror violation: {_ROUTES_FILE} imports {imp}"
            )


class TestLockR2:
    """Lock-R2: recipient_classifier.py must not import src.graph.*."""

    def test_recipient_classifier_no_graph_import(self):
        """recipient_classifier.py must not import src.graph.* (Lock-R2)."""
        rc_file = _VOICE_TONE_DIR / "recipient_classifier.py"
        assert rc_file.exists(), f"{rc_file} not found"
        imports = _get_imports(rc_file)

        for imp in imports:
            assert not imp.startswith("src.graph"), (
                f"Lock-R2 violation: recipient_classifier.py imports {imp}"
            )


class TestLockR3:
    """Lock-R3: role_resolver.py is the SOLE voice_tone module importing src.graph.arcade_client."""

    def test_role_resolver_is_sole_graph_importer(self):
        """Only role_resolver.py may import src.graph.arcade_client among voice_tone modules."""
        graph_importers = []

        for py_file in sorted(_VOICE_TONE_DIR.glob("*.py")):
            if py_file.name == "__init__.py":
                continue
            # Check both AST imports and string-level references for lazy imports
            source = py_file.read_text()
            imports = _get_imports(py_file)

            has_graph_import = any(
                imp.startswith("src.graph") for imp in imports
            )
            # Also check for lazy imports inside function bodies
            has_lazy_graph = "src.graph.arcade_client" in source and py_file.name != "__init__.py"

            if has_graph_import or has_lazy_graph:
                graph_importers.append(py_file.name)

        assert graph_importers == ["role_resolver.py"], (
            f"Lock-R3 violation: expected only role_resolver.py to import "
            f"src.graph.arcade_client, but found: {graph_importers}"
        )
