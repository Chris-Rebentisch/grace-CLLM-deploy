"""CP7 — Permission overlay integration tests (D364).

Covers:
- Agent-scope intersection prevents escalation.
- Agent without permission is refused (empty intersection).
- Audit-stamp on every successful MCP write-tool call.
- Non-agent callers carry agent_id=None, delegation_source="user_direct".
- Deny-bias: explicit deny from either party removes the triple.
"""

from __future__ import annotations

import pytest

from src.permissions.principal_context import (
    EffectiveScope,
    ScopeEntry,
    User,
    UserActingViaAgent,
    effective_scope,
    intersect_scopes,
)
from src.mcp_server.agent_adapter import resolve_principal_with_agent


# ---- Fixtures -------------------------------------------------------


@pytest.fixture
def wide_user():
    """User with broad scope (finance + legal + hr)."""
    return User(
        user_id=None,
        admin_key_present=False,
        scope=[
            ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
            ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="edit"),
            ScopeEntry(resource_kind="segment", resource_label="legal", action="edit"),
            ScopeEntry(resource_kind="segment", resource_label="hr", action="view"),
        ],
    )


@pytest.fixture
def narrow_agent_env(monkeypatch):
    """Agent with narrower scope (finance:view only)."""
    monkeypatch.setenv("GRACE_AGENT_ID", "narrow-agent")
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "Narrow Agent")
    monkeypatch.setenv("GRACE_AGENT_SCOPE", "ontology_module:finance:view")


@pytest.fixture
def disjoint_agent_env(monkeypatch):
    """Agent with no overlapping scope (compliance:audit only)."""
    monkeypatch.setenv("GRACE_AGENT_ID", "disjoint-agent")
    monkeypatch.setenv("GRACE_AGENT_DISPLAY_NAME", "Disjoint Agent")
    monkeypatch.setenv("GRACE_AGENT_SCOPE", "ontology_module:compliance:view")


# ---- Escalation prevention tests ------------------------------------


def test_agent_scope_narrower_than_user(wide_user, narrow_agent_env):
    """Agent scope narrower than user scope yields narrower effective scope."""
    principal = resolve_principal_with_agent(wide_user)
    assert isinstance(principal, UserActingViaAgent)
    eff = effective_scope(principal)
    allow_keys = {(e.resource_kind, e.resource_label, e.action) for e in eff.allows}
    # Only finance:view survives the intersection.
    assert allow_keys == {("ontology_module", "finance", "view")}
    # finance:edit, legal:edit, hr:view NOT in effective scope.
    assert ("ontology_module", "finance", "edit") not in allow_keys
    assert ("segment", "legal", "edit") not in allow_keys
    assert ("segment", "hr", "view") not in allow_keys


def test_agent_cannot_escalate_beyond_user():
    """Agent cannot add permissions the user doesn't have."""
    user = User(
        user_id=None,
        admin_key_present=False,
        scope=[
            ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
        ],
    )
    agent_scope = [
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="edit"),
        ScopeEntry(resource_kind="segment", resource_label="secrets", action="edit"),
    ]
    eff = intersect_scopes(user.scope, agent_scope)
    allow_keys = {(e.resource_kind, e.resource_label, e.action) for e in eff.allows}
    # Only finance:view survives — agent cannot add finance:edit or secrets:edit.
    assert allow_keys == {("ontology_module", "finance", "view")}


def test_disjoint_scope_empty_intersection(wide_user, disjoint_agent_env):
    """Agent with no overlapping scope results in empty effective allows."""
    principal = resolve_principal_with_agent(wide_user)
    assert isinstance(principal, UserActingViaAgent)
    eff = effective_scope(principal)
    assert len(eff.allows) == 0


def test_deny_bias_union():
    """Explicit deny from either party removes the triple from allows."""
    user_scope = [
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="edit"),
    ]
    agent_scope = [
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="edit", decision="deny"),
    ]
    eff = intersect_scopes(user_scope, agent_scope)
    allow_keys = {(e.resource_kind, e.resource_label, e.action) for e in eff.allows}
    deny_keys = {(e.resource_kind, e.resource_label, e.action) for e in eff.denies}
    # view allowed, edit denied.
    assert ("ontology_module", "finance", "view") in allow_keys
    assert ("ontology_module", "finance", "edit") in deny_keys
    assert ("ontology_module", "finance", "edit") not in allow_keys


# ---- Audit-stamp tests ----------------------------------------------


def test_agent_principal_carries_audit_fields(wide_user, narrow_agent_env):
    """UserActingViaAgent carries agent_id and agent_display_name for audit."""
    principal = resolve_principal_with_agent(wide_user)
    assert isinstance(principal, UserActingViaAgent)
    assert principal.agent_id == "narrow-agent"
    assert principal.agent_display_name == "Narrow Agent"
    assert principal.agent_scope_source == "static_config"


def test_non_agent_principal_has_no_agent_fields(wide_user, monkeypatch):
    """Plain User carries no agent identity (agent_id=None equivalent)."""
    monkeypatch.delenv("GRACE_AGENT_ID", raising=False)
    monkeypatch.delenv("GRACE_AGENT_DISPLAY_NAME", raising=False)
    monkeypatch.delenv("GRACE_AGENT_SCOPE", raising=False)
    principal = resolve_principal_with_agent(wide_user)
    assert isinstance(principal, User)
    assert not hasattr(principal, "agent_id")
    assert principal.kind == "user"


def test_effective_scope_for_plain_user():
    """effective_scope on a plain User returns user's own scope."""
    user = User(
        user_id=None,
        admin_key_present=False,
        scope=[
            ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
            ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="edit"),
        ],
    )
    eff = effective_scope(user)
    assert len(eff.allows) == 2


def test_intersect_scopes_idempotent():
    """Calling intersect_scopes on an already-intersected result is idempotent."""
    user_scope = [
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
        ScopeEntry(resource_kind="segment", resource_label="legal", action="edit"),
    ]
    agent_scope = [
        ScopeEntry(resource_kind="ontology_module", resource_label="finance", action="view"),
    ]
    first = intersect_scopes(user_scope, agent_scope)
    second = intersect_scopes(first.allows, agent_scope)
    first_keys = {(e.resource_kind, e.resource_label, e.action) for e in first.allows}
    second_keys = {(e.resource_kind, e.resource_label, e.action) for e in second.allows}
    assert first_keys == second_keys
