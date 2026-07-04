"""CP5 contract test: D246 route isolation for image_pipeline.

Verifies that src/api/extraction_routes.py does NOT import
src.extraction.image_pipeline (D246 mirror — CLI-only).
"""

from __future__ import annotations

import ast
from pathlib import Path


def test_extraction_routes_does_not_import_image_pipeline():
    """D246: extraction_routes.py must not import image_pipeline."""
    routes_path = Path(__file__).resolve().parents[2] / "src" / "api" / "extraction_routes.py"
    source = routes_path.read_text()
    tree = ast.parse(source)

    forbidden = {"image_pipeline", "src.extraction.image_pipeline"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"D246 violation: extraction_routes.py imports {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden, (
                f"D246 violation: extraction_routes.py imports from {module}"
            )
            # Also check if importing from a parent that reaches image_pipeline
            if "image_pipeline" in module:
                raise AssertionError(
                    f"D246 violation: extraction_routes.py imports from {module}"
                )
