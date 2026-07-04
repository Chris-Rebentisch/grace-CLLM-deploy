"""D246 mirror: ingestion_routes.py must NOT import src.ingestion.pipeline (CP7)."""

from __future__ import annotations

import ast
from pathlib import Path


def test_ingestion_routes_does_not_import_pipeline():
    """AST scan: src/api/ingestion_routes.py does NOT import src.ingestion.pipeline."""
    route_file = Path(__file__).resolve().parent.parent.parent / "src" / "api" / "ingestion_routes.py"
    tree = ast.parse(route_file.read_text())

    forbidden = {"src.ingestion.pipeline"}
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module in forbidden:
                violations.append(node.module)
            if node.module and any(node.module.startswith(f + ".") for f in forbidden):
                violations.append(node.module)

    assert violations == [], f"D246 violation: ingestion_routes.py imports {violations}"


def test_triage_pipeline_not_imported_in_ingestion_routes():
    """AST scan: src/api/ingestion_routes.py does NOT import src.ingestion.communications.triage.pipeline (D434/D246)."""
    route_file = Path(__file__).resolve().parent.parent.parent / "src" / "api" / "ingestion_routes.py"
    tree = ast.parse(route_file.read_text())

    forbidden = {
        "src.ingestion.communications.triage.pipeline",
        "src.ingestion.communications.triage",
    }
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden:
                    violations.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module in forbidden:
                violations.append(node.module)
            if node.module and any(node.module.startswith(f + ".") for f in forbidden):
                violations.append(node.module)

    assert violations == [], f"D434/D246 violation: ingestion_routes.py imports {violations}"
