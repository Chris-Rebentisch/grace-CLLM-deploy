"""Spec §11.2 item 4 — test_no_poisoning_patterns + AST literal check.

Per spec §8.2 the test asserts two things about every tool
description:

1. No match against the forbidden-pattern list (§8.3). Patterns
   mirror OWASP MCP Top 10 tool-poisoning indicators and Invariant
   Labs tool-poisoning research.
2. The description is a string-literal docstring — not an f-string,
   not ``str.format(...)``, not concatenation with imported names.
   An AST walk over ``src/mcp_server/tools_*.py`` catches dynamic
   construction at CI time.

The forbidden-pattern list is a single module-level constant
``FORBIDDEN_DESCRIPTION_PATTERNS``; ad-hoc pattern literals are
not allowed anywhere else in the test suite (spec §8.3
determinism rule).
"""

from __future__ import annotations

import ast
import pathlib
import re


# Hardcoded — extensions require a spec amendment (D182, §8.3).
FORBIDDEN_DESCRIPTION_PATTERNS: tuple[str, ...] = (
    # Prompt-injection framing
    r"<IMPORTANT>",
    r"<SYSTEM>",
    r"<system>",
    r"<admin>",
    r"\bignore previous\b",
    r"\bignore the above\b",
    r"\bdisregard\b",
    # Filesystem and exfil targets
    r"\bread the file\b",
    r"cat\s+~/",
    r"~/\.ssh",
    r"\bid_rsa\b",
    r"/etc/passwd",
    # Shell execution
    r"\bcurl\b",
    r"\bwget\b",
    r"\bsudo\b",
    r"rm\s+-rf",
    # Tool-call exfil framing
    r"<tool>",
    r"<function>",
    r"<invoke>",
    # URL schemes other than http/https/mailto
    r"file://",
    r"ftp://",
    r"\bdata:",
    r"javascript:",
)


_TOOL_SOURCE_FILES = (
    "tools_retrieval.py",
    "tools_graph.py",
    "tools_ontology.py",
    "tools_discovery.py",
    "tools_change_directives.py",
    "tools_meta.py",
    "tools_session.py",
    "tools_review.py",
)


def _mcp_source_dir() -> pathlib.Path:
    # tests/mcp_server/test_tool_description_safety.py -> parents[2] is repo root.
    return (
        pathlib.Path(__file__).resolve().parents[2]
        / "src"
        / "mcp_server"
    )


def _iter_tool_functions():
    from src.mcp_server import (  # noqa: F401
        tools_change_directives,
        tools_discovery,
        tools_graph,
        tools_meta,
        tools_ontology,
        tools_retrieval,
        tools_session,
        tools_review,
    )
    from src.mcp_server.server import mcp

    for _name, tool in mcp._tool_manager._tools.items():
        yield tool.fn


def _is_tool_function(node: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        func = dec.func
        # @mcp.tool()
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "tool"
            and isinstance(func.value, ast.Name)
            and func.value.id == "mcp"
        ):
            return True
        # @readonly_tool("GET", "/...") or @writable_review_tool("POST", "/...")
        if isinstance(func, ast.Name) and func.id in (
            "readonly_tool",
            "writable_review_tool",
        ):
            return True
    return False


def test_no_poisoning_patterns():
    compiled = [
        re.compile(p, re.IGNORECASE)
        for p in FORBIDDEN_DESCRIPTION_PATTERNS
    ]
    scanned = 0
    for fn in _iter_tool_functions():
        desc = (fn.__doc__ or "").strip()
        assert desc, f"{fn.__name__} missing docstring"
        for pat in compiled:
            assert not pat.search(desc), (
                f"{fn.__name__} description matches forbidden "
                f"pattern {pat.pattern!r}"
            )
        scanned += 1
    # Also AST-verify every tool function in tools_*.py has a literal
    # string docstring (spec §8.2 dynamic-construction guard).
    src_dir = _mcp_source_dir()
    assert src_dir.is_dir(), src_dir
    ast_scanned = 0
    for fname in _TOOL_SOURCE_FILES:
        path = src_dir / fname
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(
                node, (ast.AsyncFunctionDef, ast.FunctionDef)
            ):
                continue
            if not _is_tool_function(node):
                continue
            assert node.body, (
                f"{fname}:{node.name} has empty body"
            )
            first = node.body[0]
            assert isinstance(first, ast.Expr), (
                f"{fname}:{node.name}: first stmt not a bare expression"
            )
            assert isinstance(first.value, ast.Constant), (
                f"{fname}:{node.name}: docstring is not a literal"
            )
            assert isinstance(first.value.value, str), (
                f"{fname}:{node.name}: docstring literal is not str"
            )
            ast_scanned += 1
    assert scanned == 27
    assert ast_scanned == 27
