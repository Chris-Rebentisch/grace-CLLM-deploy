"""Invocation-surface lint (Chunk 40 + Chunk 41, §24.1, spec §11 / §7.5).

The decomposition pipeline is CLI-only (D246 mirror). This module
guards three architectural invariants by static-string scan over the
on-disk module bodies — independent of import-time side effects:

1. No file under ``src/decomposition/`` imports ``fastapi``.
2. No file under ``src/decomposition/`` imports ``apscheduler``.
3. No file under ``src/api/`` imports
   ``src.decomposition.pipeline.orchestrator`` (Chunk 41 narrowed:
   the routes layer is allowed to call repository / decision /
   adapter helpers but must NEVER reach the heavy Layers 1–4
   orchestrator — that path is reserved for the CLI subprocess
   spawned by ``POST /api/decomposition/runs/trigger``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
DECOMP_DIR = REPO_ROOT / "src" / "decomposition"
API_DIR = REPO_ROOT / "src" / "api"


_FASTAPI_PAT = re.compile(r"^\s*(import\s+fastapi\b|from\s+fastapi(\.|\s))", re.MULTILINE)
_APS_PAT = re.compile(r"^\s*(import\s+apscheduler\b|from\s+apscheduler(\.|\s))", re.MULTILINE)
# Chunk 41 (D328 / spec §7.5 / §11.5): only the orchestrator path is
# forbidden. Routes may import models / repositories / decision +
# adapter modules under src.decomposition.* directly.
_DECOMP_IMPORT_PAT = re.compile(
    r"^\s*(import\s+src\.decomposition\.pipeline\.orchestrator\b"
    r"|from\s+src\.decomposition\.pipeline\.orchestrator(\.|\s))",
    re.MULTILINE,
)


def _python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_decomposition_does_not_import_fastapi():
    """No file under src/decomposition/ imports fastapi (D246 mirror)."""
    offenders: list[str] = []
    for f in _python_files(DECOMP_DIR):
        text = f.read_text(encoding="utf-8")
        if _FASTAPI_PAT.search(text):
            offenders.append(str(f.relative_to(REPO_ROOT)))
    assert not offenders, (
        "src/decomposition/ must not import fastapi. Offenders: "
        f"{offenders}"
    )


def test_decomposition_does_not_import_apscheduler():
    """No file under src/decomposition/ imports apscheduler (D246 mirror)."""
    offenders: list[str] = []
    for f in _python_files(DECOMP_DIR):
        text = f.read_text(encoding="utf-8")
        if _APS_PAT.search(text):
            offenders.append(str(f.relative_to(REPO_ROOT)))
    assert not offenders, (
        "src/decomposition/ must not import apscheduler. Offenders: "
        f"{offenders}"
    )


def test_api_does_not_import_decomposition_orchestrator():
    """No file under src/api/ imports src.decomposition.pipeline.orchestrator.

    Chunk 41 narrows the original chunk-40 lint: the routes layer is
    allowed to call repositories / decision modules / adapters under
    ``src.decomposition.*``, but the heavy Layer 1–4 orchestrator
    must remain CLI-only (D246/D315). Trigger route spawns the CLI
    via ``subprocess.Popen``.
    """
    offenders: list[str] = []
    for f in _python_files(API_DIR):
        text = f.read_text(encoding="utf-8")
        if _DECOMP_IMPORT_PAT.search(text):
            offenders.append(str(f.relative_to(REPO_ROOT)))
    assert not offenders, (
        "src/api/ must not import src.decomposition.pipeline.orchestrator. "
        f"Offenders: {offenders}"
    )
