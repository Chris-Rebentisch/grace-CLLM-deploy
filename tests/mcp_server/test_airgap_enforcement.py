"""Spec §11.2 item 5 — airgap enforcement (D183).

Three subtests in one file:

* ``test_non_loopback_hostname_raises_at_startup`` — CLI startup
  validation refuses a non-loopback literal.
* ``test_resolved_ip_rebinding_raises_per_request`` —
  ``_assert_loopback`` refuses a resolved address that is not
  loopback (DNS-rebinding defense).
* ``test_no_cloud_sdk_imports`` — no module under
  ``src/mcp_server/`` imports a cloud LLM SDK.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
import socket

import pytest

from src.mcp_server.errors import MCPAirgapViolation


def test_non_loopback_hostname_raises_at_startup(monkeypatch):
    """Patch ``MCP_GRACE_BASE_URL`` to a non-loopback literal and run
    the CLI startup validation. Must raise ``MCPAirgapViolation``
    before any outbound request is possible (§5.3)."""
    monkeypatch.setenv(
        "MCP_GRACE_BASE_URL", "http://example.com:8000"
    )

    # Re-import cli so _startup_validate picks up the patched env.
    # (It reads os.environ at call time, so this is defensive —
    # existing import state is fine to reuse.)
    from src.mcp_server.cli import _startup_validate

    with pytest.raises(MCPAirgapViolation):
        asyncio.run(_startup_validate())


def test_non_http_scheme_rejected_at_startup(monkeypatch):
    """Startup config must require MCP_GRACE_BASE_URL scheme http/https."""
    monkeypatch.setenv("MCP_GRACE_BASE_URL", "ftp://127.0.0.1:8000")

    from src.mcp_server.cli import _startup_validate

    with pytest.raises(ValueError, match="http or https"):
        asyncio.run(_startup_validate())


def test_resolved_ip_rebinding_raises_per_request(monkeypatch):
    """Stub ``socket.getaddrinfo`` to return a non-loopback address
    for a loopback literal and call ``_assert_loopback`` directly.
    Must raise ``MCPAirgapViolation`` (§5.4).

    This is the DNS-rebinding defense: an attacker DNS server
    could respond with ``127.0.0.1`` at startup and a public IP
    at request time; per-request re-resolution catches the swap.
    """
    from src.mcp_server.http_client import _assert_loopback

    def _fake(host: str, port: int, *args, **kwargs):
        # Loopback literal input, non-loopback resolved result.
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                0,
                "",
                ("8.8.8.8", port),
            )
        ]

    monkeypatch.setattr(socket, "getaddrinfo", _fake)
    with pytest.raises(MCPAirgapViolation):
        asyncio.run(_assert_loopback("localhost", 8000))


def test_no_cloud_sdk_imports():
    """Static AST walk: no file under ``src/mcp_server/`` imports
    any cloud-provider SDK (§5.5, §11.2 item 5). A transitive
    dependency that pulls in one of these is a scope violation."""
    forbidden_tops = frozenset(
        {
            "anthropic",
            "openai",
            "deepseek",
            "groq",
            "cohere",
            "mistralai",
            "together",
            "replicate",
        }
    )
    # google.generativeai is tracked at its top-level name "google",
    # but "google" alone is too broad (grpc, protobuf, etc.). Check
    # for the dotted module path explicitly.
    forbidden_dotted = frozenset(
        {
            "google.generativeai",
            "google.genai",
        }
    )

    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "mcp_server"
    assert root.is_dir(), root
    violations: list[str] = []
    for py_file in sorted(root.rglob("*.py")):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top in forbidden_tops:
                        violations.append(
                            f"{py_file.name}: import {alias.name}"
                        )
                    if alias.name in forbidden_dotted:
                        violations.append(
                            f"{py_file.name}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top = module.split(".")[0]
                if top in forbidden_tops:
                    violations.append(
                        f"{py_file.name}: from {module} import ..."
                    )
                if module in forbidden_dotted:
                    violations.append(
                        f"{py_file.name}: from {module} import ..."
                    )
    assert not violations, (
        "Cloud-provider SDK imports detected under src/mcp_server/: "
        + "; ".join(violations)
    )
