"""CI guard: import boundary enforcement for federation_router.py (Chunk 52).

Ensures the federation router only imports allowed modules from
src/retrieval/ and never imports pipeline.py or cypher_rewriter.
"""

from __future__ import annotations

import ast
import pathlib


_ROUTER_PATH = pathlib.Path("src/retrieval/federation_router.py")


def _get_imports(filepath: pathlib.Path) -> set[str]:
    """Extract all import module paths from a Python file via AST."""
    source = filepath.read_text()
    tree = ast.parse(source)

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)

    return imports


def test_retrieval_imports_limited_to_fusion_and_models():
    """federation_router.py only imports fusion.py and retrieval_models.py from src/retrieval/."""
    imports = _get_imports(_ROUTER_PATH)

    retrieval_imports = {
        m for m in imports if m.startswith("src.retrieval.")
    }

    allowed = {
        "src.retrieval.fusion",
        "src.retrieval.retrieval_models",
    }

    unexpected = retrieval_imports - allowed
    assert not unexpected, (
        f"federation_router.py imports unexpected src/retrieval/ modules: {unexpected}. "
        f"Only fusion.py and retrieval_models.py are allowed (CF3 + D384)."
    )


def test_no_pipeline_import():
    """federation_router.py must NOT import pipeline — query capability is via NamespaceQueryFn."""
    imports = _get_imports(_ROUTER_PATH)

    pipeline_imports = {
        m for m in imports
        if "pipeline" in m.split(".")[-1]
    }

    assert not pipeline_imports, (
        f"federation_router.py imports pipeline module: {pipeline_imports}. "
        f"The router receives query capability via NamespaceQueryFn injection."
    )


def test_no_cypher_rewriter_import():
    """federation_router.py must NOT import cypher_rewriter — deferred (spec §14)."""
    imports = _get_imports(_ROUTER_PATH)

    rewriter_imports = {
        m for m in imports
        if "cypher_rewriter" in m
    }

    assert not rewriter_imports, (
        f"federation_router.py imports cypher_rewriter: {rewriter_imports}. "
        f"Cypher rewriter namespace extension is deferred (§14)."
    )
