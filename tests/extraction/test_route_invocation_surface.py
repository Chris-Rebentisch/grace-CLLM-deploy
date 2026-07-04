"""D246 mirror CI guard — extraction_routes.py must NOT import
batch_runner or eval_checkpoint (D470).
"""

from __future__ import annotations

import ast


def test_extraction_routes_no_forbidden_imports():
    """Assert extraction_routes.py does NOT import batch_runner or eval_checkpoint."""
    source_path = "src/api/extraction_routes.py"
    with open(source_path) as f:
        source = f.read()
    tree = ast.parse(source)

    forbidden = {"src.discovery.batch_runner", "src.extraction.eval_checkpoint"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden, (
                    f"extraction_routes.py imports forbidden module: {alias.name}"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module and any(node.module.startswith(f) for f in forbidden):
                raise AssertionError(
                    f"extraction_routes.py imports from forbidden module: {node.module}"
                )
