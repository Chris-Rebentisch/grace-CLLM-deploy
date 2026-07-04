"""MCP tool gate decorator tests (Chunk 42, CP9, D335).

Coverage:

* Decorator preserves the wrapped function's name + ``__grace_route__``
  for tool inventory parity (T1).
* When the active matrix admits the principal, the gate is a pass-through
  and the wrapped function executes (T2).
* When the active matrix denies the principal, ``PermissionError`` is
  raised before the wrapped function runs (T3).
* No active matrix → default-deny (OWASP A01) — the gate raises (T4).
* Decorator works on async functions too — coroutine return values
  propagate (T5).
* F-032e / ISS-0022 enforcement-plane parity: the gate honors
  ``permission_enforcement_enabled()`` (GRACE_PERMISSION_ENFORCEMENT_ENABLED,
  default OFF) exactly like the REST middleware — pass-through when
  disabled, enforce when enabled (T6).
"""

from __future__ import annotations

import asyncio

import pytest

from src.mcp_server.server import permission_gated_tool
from src.permissions.enforcer import rebuild_enforcer
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
)


@pytest.fixture(autouse=True)
def _reset_enforcer():
    rebuild_enforcer(None)
    yield
    rebuild_enforcer(None)


@pytest.fixture(autouse=True)
def _enable_enforcement(monkeypatch):
    """F-032e / ISS-0022: the gate now mirrors the REST plane's env toggle
    (default OFF → pass-through). The denial tests below (T3/T4) assert
    enforcement behavior, so enable it for this module; the T6 parity tests
    override per-test."""
    monkeypatch.setenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", "1")


def _admit_all_matrix(allow_anonymous: bool = True) -> PermissionMatrix:
    """Matrix with ``default_decision="allow"`` so the anonymous caller
    used by the MCP gate (``user_id=None``) is admitted under the
    matrix-level fallback."""
    return PermissionMatrix(
        role_clusters=[],
        default_decision="allow" if allow_anonymous else "deny",
    )


# ---------- T1: decorator preserves identity --------------------


def test_decorator_preserves_route_metadata_and_name() -> None:
    def base() -> str:
        return "ok"

    base.__grace_route__ = ("POST", "/api/retrieval/query")  # type: ignore[attr-defined]

    wrapped = permission_gated_tool(
        "graph_entity", "global", "view"
    )(base)

    assert wrapped.__name__ == "base"
    assert getattr(wrapped, "__grace_route__", None) == (
        "POST",
        "/api/retrieval/query",
    )
    assert getattr(wrapped, "__grace_permission_gate__", None) == (
        "graph_entity",
        "global",
        "view",
    )


# ---------- T2: pass-through under allow-default matrix ---------


def test_gate_passes_through_when_matrix_allows() -> None:
    rebuild_enforcer(_admit_all_matrix(allow_anonymous=True))

    calls: list[int] = []

    @permission_gated_tool("graph_entity", "global", "view")
    def tool() -> str:
        calls.append(1)
        return "ran"

    assert tool() == "ran"
    assert calls == [1]


# ---------- T3: explicit deny raises PermissionError ------------


def test_gate_raises_on_explicit_deny() -> None:
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="anon",
                display_name="Anon",
                members=[],
                access_rules=[],
            )
        ],
        default_decision="deny",
    )
    rebuild_enforcer(matrix)

    @permission_gated_tool("graph_entity", "global", "view")
    def tool() -> str:
        return "should-not-run"

    with pytest.raises(PermissionError) as exc:
        tool()
    # Reason code surfaces in the message for operator triage.
    assert "denied" in str(exc.value)


# ---------- T4: no active matrix → default-deny -----------------


def test_gate_default_denies_when_no_active_matrix() -> None:
    # autouse fixture has already cleared the matrix.
    @permission_gated_tool("graph_entity", "global", "view")
    def tool() -> str:
        return "should-not-run"

    with pytest.raises(PermissionError) as exc:
        tool()
    assert "no_active_matrix" in str(exc.value)


# ---------- T5: async tool support ------------------------------


def test_gate_supports_async_tool() -> None:
    rebuild_enforcer(_admit_all_matrix(allow_anonymous=True))

    @permission_gated_tool("graph_entity", "global", "view")
    async def tool() -> str:
        return "async-ok"

    result = asyncio.run(tool())
    assert result == "async-ok"


# ---------- T6: F-032e / ISS-0022 enforcement-plane parity -------


def test_gate_passes_through_when_enforcement_env_unset(monkeypatch) -> None:
    """With GRACE_PERMISSION_ENFORCEMENT_ENABLED unset (the default posture)
    and NO active matrix, the write tool must run — exactly like the REST
    API, which skips the permission middleware entirely."""
    monkeypatch.delenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", raising=False)
    # autouse fixture already cleared the matrix → previously no_active_matrix.

    calls: list[int] = []

    @permission_gated_tool("extraction_claim", "global", "edit")
    def tool() -> str:
        calls.append(1)
        return "ran"

    assert tool() == "ran"
    assert calls == [1]


def test_gate_passes_through_when_enforcement_disabled_despite_deny_matrix(
    monkeypatch,
) -> None:
    """Explicit '0' disables enforcement even when a default-deny matrix is
    loaded — plane parity with the REST middleware's early return."""
    monkeypatch.setenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", "0")
    rebuild_enforcer(_admit_all_matrix(allow_anonymous=False))

    @permission_gated_tool("extraction_claim", "global", "edit")
    def tool() -> str:
        return "ran"

    assert tool() == "ran"


def test_gate_enforces_when_enforcement_enabled(monkeypatch) -> None:
    """With the flag set to 1 and no active matrix, the gate denies with
    no_active_matrix — both planes enforce."""
    monkeypatch.setenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", "1")

    @permission_gated_tool("extraction_claim", "global", "edit")
    def tool() -> str:
        return "should-not-run"

    with pytest.raises(PermissionError) as exc:
        tool()
    assert "no_active_matrix" in str(exc.value)


def test_gate_enforces_async_when_enforcement_enabled(monkeypatch) -> None:
    monkeypatch.setenv("GRACE_PERMISSION_ENFORCEMENT_ENABLED", "1")

    @permission_gated_tool("extraction_claim", "global", "edit")
    async def tool() -> str:
        return "should-not-run"

    with pytest.raises(PermissionError):
        asyncio.run(tool())
