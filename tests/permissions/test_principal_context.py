"""Tests for ``src.permissions.principal_context`` (Chunk 42, CP2, D338)."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from src.permissions.principal_context import (
    EffectiveScope,
    PrincipalContext,
    ScopeEntry,
    User,
    UserActingViaAgent,
    effective_scope,
    from_admission_tree,
    intersect_scopes,
    is_strict_visibility,
)


def _entry(kind: str, label: str, action: str = "view", decision: str = "allow") -> ScopeEntry:
    return ScopeEntry(
        resource_kind=kind,  # type: ignore[arg-type]
        resource_label=label,
        action=action,  # type: ignore[arg-type]
        decision=decision,  # type: ignore[arg-type]
    )


def test_user_variant_constructs_with_uuid() -> None:
    uid = uuid4()
    user = User(user_id=uid, display_name="Alice", admin_key_present=False)
    assert user.kind == "user"
    assert user.user_id == uid
    assert user.scope == []


def test_user_via_agent_variant_constructs_with_static_config_default() -> None:
    agent = UserActingViaAgent(
        user_id=uuid4(),
        agent_id="agent-1",
        agent_display_name="research-bot",
    )
    assert agent.kind == "user_via_agent"
    assert agent.agent_scope_source == "static_config"


def test_principal_context_discriminated_union_dispatch() -> None:
    """The ``PrincipalContext`` annotation discriminates on ``kind``."""
    from pydantic import TypeAdapter

    adapter = TypeAdapter(PrincipalContext)
    user_payload = {"kind": "user", "admin_key_present": False}
    agent_payload = {
        "kind": "user_via_agent",
        "agent_id": "a-1",
        "user_scope": [],
        "agent_scope": [],
    }
    assert isinstance(adapter.validate_python(user_payload), User)
    assert isinstance(adapter.validate_python(agent_payload), UserActingViaAgent)


def test_from_admission_tree_returns_user_variant() -> None:
    """Factory MUST NOT return ``UserActingViaAgent`` (Chunk 44 dormancy)."""
    uid = uuid4()
    request = SimpleNamespace(
        state=SimpleNamespace(
            user_id=uid,
            user_display_name="Bob",
            admin_key_present=True,
        )
    )
    principal = from_admission_tree(request)
    assert isinstance(principal, User)
    assert principal.kind == "user"
    assert principal.user_id == uid
    assert principal.display_name == "Bob"
    assert principal.admin_key_present is True


def test_from_admission_tree_handles_missing_state_safely() -> None:
    request = SimpleNamespace()  # no state attribute
    principal = from_admission_tree(request)
    assert isinstance(principal, User)
    assert principal.user_id is None
    assert principal.admin_key_present is False


def test_from_admission_tree_coerces_uuid_string() -> None:
    uid = uuid4()
    request = SimpleNamespace(
        state=SimpleNamespace(
            user_id=str(uid),
            user_display_name=None,
            admin_key_present=False,
        )
    )
    principal = from_admission_tree(request)
    assert principal.user_id == uid


def test_is_strict_visibility_returns_true_for_private_to_self() -> None:
    assert is_strict_visibility("private_to_self") is True


def test_is_strict_visibility_returns_false_for_other_modes() -> None:
    for mode in (
        "permission_matrix_default",
        "private_to_named_list",
        "scoped_to_role_cluster",
        "unknown_mode",
    ):
        assert is_strict_visibility(mode) is False


def test_intersect_scopes_basic_overlap() -> None:
    user_scope = [_entry("ontology_module", "finance"), _entry("segment", "ops")]
    agent_scope = [_entry("ontology_module", "finance"), _entry("segment", "hr")]
    eff = intersect_scopes(user_scope, agent_scope)
    keys = {(e.resource_kind, e.resource_label, e.action) for e in eff.allows}
    assert keys == {("ontology_module", "finance", "view")}


def test_effective_scope_for_user_promotes_allows_and_denies() -> None:
    uid = uuid4()
    user = User(
        user_id=uid,
        scope=[
            _entry("ontology_module", "finance"),
            _entry("segment", "secret", decision="deny"),
        ],
    )
    eff = effective_scope(user)
    assert isinstance(eff, EffectiveScope)
    assert len(eff.allows) == 1
    assert len(eff.denies) == 1
    assert eff.admits("ontology_module", "finance", "view") is True
    assert eff.admits("segment", "secret", "view") is False
