"""Repo-guard: no bare asyncio.get_event_loop() under src/ (F-56).

Validation run: asyncio.get_event_loop() raises RuntimeError on Python
3.14 when there is no running loop (the normal CLI case). Every call site must
use the try/except-RuntimeError idiom via asyncio.get_running_loop() instead.

F2-17 rework: the original line-scan stripped `#` comments but not DOCSTRINGS,
so the F-56 fix's own capture-the-why docstring (which *mentions* the banned
call) tripped the guard. The guard now walks the AST and flags only ACTUAL
call expressions `asyncio.get_event_loop(...)` — comments and string literals
can never match.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"


def _bare_call_sites(path: Path) -> list[int]:
    """Line numbers of real `asyncio.get_event_loop()` call expressions."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []  # unparseable file is some other test's problem
    sites: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get_event_loop"
            and isinstance(func.value, ast.Name)
            and func.value.id == "asyncio"
        ):
            sites.append(node.lineno)
    return sites


def test_no_bare_get_event_loop_in_src():
    offenders: list[str] = []
    for py in _SRC.rglob("*.py"):
        for lineno in _bare_call_sites(py):
            offenders.append(f"{py.relative_to(_REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "Bare asyncio.get_event_loop() call found (F-56 — raises on Python 3.14 "
        "with no running loop). Use asyncio.get_running_loop() + except RuntimeError:\n"
        + "\n".join(offenders)
    )


def test_guard_ignores_docstrings_and_comments(tmp_path):
    """F2-17 regression: mentions in docstrings/comments must NOT trip the guard."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        '"""This docstring mentions asyncio.get_event_loop() harmlessly."""\n'
        "# comment: asyncio.get_event_loop() is banned\n"
        "x = 1\n"
    )
    assert _bare_call_sites(clean) == []

    dirty = tmp_path / "dirty.py"
    dirty.write_text("import asyncio\nloop = asyncio.get_event_loop()\n")
    assert _bare_call_sites(dirty) == [2]
