"""CLASS-KILLER (a): import-smoke for every CLI entrypoint under src/.

A validation run surfaced a phantom-import / interface-drift class (F-12,
F-34, F-55): CLI modules that a green test suite never actually imported, so a
`from src.api.database import ...` (nonexistent) or a `client.query(...)` (no
such method) shipped and only blew up at runtime.

This test dynamically enumerates every module under src/ that has an
`if __name__ == "__main__"` block (the CLI entrypoints — also the modules the
CLAUDE.md `python -m ...` runbook invokes) and asserts each imports without
ModuleNotFoundError / ImportError.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"


def _discover_cli_modules() -> list[str]:
    """Every src/ module carrying an `if __name__ == '__main__'` block."""
    mods: list[str] = []
    for py in _SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        if 'if __name__ == "__main__"' not in text and "if __name__ == '__main__'" not in text:
            continue
        rel = py.relative_to(_REPO_ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__main__":
            parts = parts[:-1]
        mods.append(".".join(parts))
    return sorted(set(mods))


_CLI_MODULES = _discover_cli_modules()


def test_cli_modules_discovered():
    """Sanity: we actually found the known CLI entrypoints (guards the discovery)."""
    assert len(_CLI_MODULES) >= 30
    for expected in (
        "src.extraction.extraction_bridge",
        "src.extraction.image_pipeline",
        "src.ingestion.communications.voice_tone",
        "src.ingestion.communications.thread_reconstructor",
        "src.connectors",  # src/connectors/__main__.py
        "src.decomposition.pipeline",
    ):
        assert expected in _CLI_MODULES, f"{expected} not discovered among {_CLI_MODULES}"


@pytest.mark.parametrize("module_name", _CLI_MODULES)
def test_cli_module_imports(module_name):
    """Each CLI entrypoint must import without ModuleNotFoundError / ImportError."""
    try:
        importlib.import_module(module_name)
    except (ModuleNotFoundError, ImportError) as exc:
        pytest.fail(
            f"CLI module {module_name} failed to import (phantom-import class): "
            f"{type(exc).__name__}: {exc}"
        )
