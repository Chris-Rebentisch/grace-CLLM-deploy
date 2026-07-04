"""D270 single-engine invariant — AST guard against admission logic
leaking into the Sensitivity Gate (Chunk 43, CP2 / D343).

Static-analyzes ``src/permissions/sensitivity_subset.py`` to assert it
does NOT import ``src.permissions.enforcer`` or any aliased equivalent.
The Sensitivity Gate is render-only; if a future edit reaches for the
enforcer to "decide" what to render, this test breaks the build.
"""

from __future__ import annotations

import ast
import pathlib

import pytest


_SUBSET_PATH = pathlib.Path("src/permissions/sensitivity_subset.py")
_REPORT_PATH = pathlib.Path("src/permissions/sensitivity_report.py")


def _load_module_ast(path: pathlib.Path = _SUBSET_PATH) -> ast.Module:
    text = path.read_text(encoding="utf-8")
    return ast.parse(text, filename=str(path))


def test_sensitivity_subset_does_not_import_enforcer():
    """``sensitivity_subset.py`` MUST NOT import the enforcer module
    (D270/D343)."""
    if not _SUBSET_PATH.exists():
        pytest.fail(f"missing source file: {_SUBSET_PATH}")
    tree = _load_module_ast()

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "enforcer" in alias.name:
                    offending.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "enforcer" in mod:
                offending.append(f"from {mod} import ...")
            for alias in node.names:
                if "enforcer" in alias.name:
                    offending.append(f"from {mod} import {alias.name}")

    assert not offending, (
        "D270 violation — sensitivity_subset.py reaches for the "
        f"enforcer: {offending}"
    )


def test_sensitivity_subset_does_not_import_repository_or_db():
    """Render-only invariant extends to no DB I/O — assert no
    SQLAlchemy / Postgres / ArcadeDB import surface."""
    tree = _load_module_ast()
    forbidden_substrings = (
        "sqlalchemy",
        "psycopg",
        "arcade_client",
        "permissions.repository",
        "ontology.database",
    )
    offending: list[str] = []
    for node in ast.walk(tree):
        target_names: list[str] = []
        if isinstance(node, ast.Import):
            target_names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            target_names = [mod] + [a.name for a in node.names]
        for nm in target_names:
            for needle in forbidden_substrings:
                if needle in nm:
                    offending.append(nm)

    assert not offending, (
        "D270/D343 violation — sensitivity_subset.py performs DB I/O: "
        f"{offending}"
    )


def test_sensitivity_report_does_not_import_enforcer():
    """``sensitivity_report.py`` MUST NOT import the enforcer module
    (D270/D343) — same render-only invariant as the subset projector."""
    if not _REPORT_PATH.exists():
        pytest.fail(f"missing source file: {_REPORT_PATH}")
    tree = _load_module_ast(_REPORT_PATH)

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "enforcer" in alias.name:
                    offending.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "enforcer" in mod:
                offending.append(f"from {mod} import ...")
            for alias in node.names:
                if "enforcer" in alias.name:
                    offending.append(f"from {mod} import {alias.name}")

    assert not offending, (
        "D270 violation — sensitivity_report.py reaches for the "
        f"enforcer: {offending}"
    )


def test_sensitivity_report_does_not_perform_db_io():
    """The pure-function generator MUST NOT import any DB primitive —
    persistence is the repository's job, not the generator's (D343)."""
    tree = _load_module_ast(_REPORT_PATH)
    forbidden_substrings = (
        "sqlalchemy",
        "psycopg",
        "arcade_client",
        "permissions.repository",
        "permissions.sensitivity_repository",
        "ontology.database",
    )
    offending: list[str] = []
    for node in ast.walk(tree):
        target_names: list[str] = []
        if isinstance(node, ast.Import):
            target_names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            target_names = [mod] + [a.name for a in node.names]
        for nm in target_names:
            for needle in forbidden_substrings:
                if needle in nm:
                    offending.append(nm)

    assert not offending, (
        "D270/D343 violation — sensitivity_report.py performs DB I/O: "
        f"{offending}"
    )


def test_sensitivity_resolver_does_not_import_enforcer():
    """``sensitivity_resolver.py`` MUST NOT import the enforcer module
    (D270/D521 — shared derivation helper is pure-function, not an
    enforcement engine)."""
    resolver_path = pathlib.Path("src/permissions/sensitivity_resolver.py")
    if not resolver_path.exists():
        pytest.fail(f"missing source file: {resolver_path}")
    tree = _load_module_ast(resolver_path)

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "enforcer" in alias.name:
                    offending.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "enforcer" in mod:
                offending.append(f"from {mod} import ...")
            for alias in node.names:
                if "enforcer" in alias.name:
                    offending.append(f"from {mod} import {alias.name}")

    assert not offending, (
        "D270/D521 violation — sensitivity_resolver.py reaches for the "
        f"enforcer: {offending}"
    )


def test_no_second_enforcement_engine_in_permissions():
    """D270 single-engine invariant — only ``enforcer.py`` may contain a
    class named ``Enforcer`` or function named ``enforce`` in
    ``src/permissions/``. D521 domain-entity sensitivity flows through
    the existing Enforcer and cypher rewriter, never a new engine."""
    perm_dir = pathlib.Path("src/permissions")
    enforcer_file = perm_dir / "enforcer.py"

    offending: list[str] = []
    for py_file in perm_dir.glob("*.py"):
        if py_file == enforcer_file:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "Enforcer":
                offending.append(f"{py_file.name}: class Enforcer")
            if isinstance(node, ast.FunctionDef) and node.name == "enforce":
                offending.append(f"{py_file.name}: def enforce()")

    assert not offending, (
        "D270 violation — second enforcement engine found in "
        f"src/permissions/: {offending}"
    )


def test_enforcer_does_not_import_ingestion_communications():
    """``enforcer.py`` MUST NOT import ``src.ingestion.communications.*``
    (D270 — Chunk 59 CP6; sensitivity tags are fetch-time annotation,
    not enforcement admission)."""
    enforcer_path = pathlib.Path("src/permissions/enforcer.py")
    if not enforcer_path.exists():
        pytest.fail(f"missing source file: {enforcer_path}")
    tree = _load_module_ast(enforcer_path)

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if "src.ingestion.communications" in alias.name:
                    offending.append(f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if "src.ingestion.communications" in mod:
                offending.append(f"from {mod} import ...")

    assert not offending, (
        "D270 violation — enforcer.py imports ingestion communications "
        f"modules: {offending}"
    )
