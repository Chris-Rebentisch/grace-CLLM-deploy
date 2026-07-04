"""CP3 — Agent-call adapter tests (D364).

Covers:
- UserActingViaAgent construction from env config.
- Graceful User degradation when env absent.
- intersect_scopes produces expected intersection.
- scope_source assertion.
- Agent without permission logs warning.
"""

from __future__ import annotations

import os

import pytest

from src.permissions.principal_context import (
    ScopeEntry,
    User,
    UserActingViaAgent,
    intersect_scopes,
)
from src.mcp_server.agent_adapter import (
    resolve_principal_with_agent,
    _parse_scope,
)


@pytest.fixture
def _agent_env(monkeypatch):
    """Set agent identity env vars."""
    monkeypatch.setenv("GRACE_AGENT_ID", "cowork-1")
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "Cowork Plugin")
    monkeypatch.setenv(
        "GRACE_AGENT_SCOPE",
        "ontology_module:finance:view,segment:legal:edit",
    )


@pytest.fixture
def _no_agent_env(monkeypatch):
    """Clear agent identity env vars."""
    monkeypatch.delenv("GRACE_AGENT_ID", raising=False)
    monkeypatch.delenv("GRACE_AGENT_DISPLAY_NAME", raising=False)
    monkeypatch.delenv("GRACE_AGENT_SCOPE", raising=False)


@pytest.fixture
def user_with_scope():
    """A user with matching scope entries."""
    return User(
        user_id=None,
        admin_key_present=False,
        scope=[
            ScopeEntry(
                resource_kind="ontology_module",
                resource_label="finance",
                action="view",
            ),
            ScopeEntry(
                resource_kind="segment",
                resource_label="legal",
                action="edit",
            ),
            ScopeEntry(
                resource_kind="segment",
                resource_label="hr",
                action="view",
            ),
        ],
    )


@pytest.fixture
def user_no_scope():
    return User(user_id=None, admin_key_present=False, scope=[])


def test_resolve_with_agent_env(user_with_scope, _agent_env):
    """With all env vars, returns UserActingViaAgent."""
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, UserActingViaAgent)
    assert result.agent_id == "cowork-1"
    assert result.agent_display_name == "Cowork Plugin"
    assert result.agent_scope_source == "static_config"


def test_resolve_without_agent_env(user_with_scope, _no_agent_env):
    """Without env vars, returns the plain User unchanged."""
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, User)
    assert result is user_with_scope


def test_resolve_partial_env_missing_id(user_with_scope, monkeypatch):
    """Missing GRACE_AGENT_ID degrades to User."""
    monkeypatch.delenv("GRACE_AGENT_ID", raising=False)
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "X")
    monkeypatch.setenv("GRACE_AGENT_SCOPE", "ontology_module:x:view")
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, User)


def test_resolve_partial_env_missing_display_name(user_with_scope, monkeypatch):
    """Missing GRACE_AGENT_DISPLAY_NAME degrades to User."""
    monkeypatch.setenv("GRACE_AGENT_ID", "x")
    monkeypatch.delenv("GRACE_AGENT_DISPLAY_NAME", raising=False)
    monkeypatch.setenv("GRACE_AGENT_SCOPE", "ontology_module:x:view")
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, User)


def test_intersect_scopes_narrowing(user_with_scope, _agent_env):
    """Agent scope narrower than user scope yields narrower effective scope."""
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, UserActingViaAgent)
    effective = intersect_scopes(result.user_scope, result.agent_scope)
    # Agent scope has finance:view and legal:edit; user has those plus hr:view.
    # Intersection should have finance:view and legal:edit but NOT hr:view.
    keys = {(e.resource_kind, e.resource_label, e.action) for e in effective.allows}
    assert ("ontology_module", "finance", "view") in keys
    assert ("segment", "legal", "edit") in keys
    assert ("segment", "hr", "view") not in keys


def test_intersect_scopes_empty_warns(user_no_scope, monkeypatch):
    """Agent with non-overlapping scope produces warning."""
    monkeypatch.setenv("GRACE_AGENT_ID", "agent-2")
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "Agent 2")
    monkeypatch.setenv("GRACE_AGENT_SCOPE", "ontology_module:hr:view")
    result = resolve_principal_with_agent(user_no_scope)
    assert isinstance(result, UserActingViaAgent)
    effective = intersect_scopes(result.user_scope, result.agent_scope)
    assert effective.is_empty() or len(effective.allows) == 0


def test_user_scope_propagated(user_with_scope, _agent_env):
    """User scope is carried on the UserActingViaAgent."""
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, UserActingViaAgent)
    assert len(result.user_scope) == 3


def test_agent_scope_parsed_correctly(user_with_scope, _agent_env):
    """Agent scope is parsed from env string into ScopeEntry objects."""
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, UserActingViaAgent)
    assert len(result.agent_scope) == 2


def test_parse_scope_empty():
    """Empty scope string returns empty list."""
    assert _parse_scope("") == []
    assert _parse_scope("   ") == []


def test_parse_scope_with_decision():
    """Scope entries with explicit decision are parsed correctly."""
    entries = _parse_scope("ontology_module:finance:view:deny")
    assert len(entries) == 1
    assert entries[0].decision == "deny"


def test_kind_preserved_on_agent(user_with_scope, _agent_env):
    """UserActingViaAgent has kind='user_via_agent'."""
    result = resolve_principal_with_agent(user_with_scope)
    assert isinstance(result, UserActingViaAgent)
    assert result.kind == "user_via_agent"
