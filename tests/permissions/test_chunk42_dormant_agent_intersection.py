"""Architect N3 four-fixture dormant-agent intersection tests (Chunk 42, D338).

These tests pin the four shapes of (user_scope, agent_scope) intersection
that Chunk 44's agent-scoped runtime path will rely on:

(a) ``user_perms ⊋ agent_perms`` — agent narrows; intersection = agent_perms.
(b) ``user_perms ⊊ agent_perms`` — agent does NOT escalate; intersection = user_perms.
(c) ``user_perms ∩ agent_perms = ∅`` — empty intersection, deny-all.
(d) ``private_to_self`` directive — strict short-circuit fires BEFORE
    intersection consults the agent scope (R4).
"""

from __future__ import annotations

from src.permissions.principal_context import (
    ScopeEntry,
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


def _allow_keys(scope) -> set[tuple[str, str, str]]:
    return {(e.resource_kind, e.resource_label, e.action) for e in scope.allows}


def test_dormant_agent_a_user_superset_of_agent() -> None:
    """(a) user_perms ⊋ agent_perms → intersection = agent_perms."""
    user_scope = [
        _entry("ontology_module", "finance"),
        _entry("ontology_module", "legal"),
        _entry("segment", "ops"),
    ]
    agent_scope = [_entry("ontology_module", "finance")]
    eff = intersect_scopes(user_scope, agent_scope)
    assert _allow_keys(eff) == {("ontology_module", "finance", "view")}


def test_dormant_agent_b_agent_superset_of_user_no_escalation() -> None:
    """(b) user_perms ⊊ agent_perms → intersection = user_perms.

    Crucial invariant: an agent NEVER escalates a user's access.
    """
    user_scope = [_entry("ontology_module", "finance")]
    agent_scope = [
        _entry("ontology_module", "finance"),
        _entry("ontology_module", "legal"),
        _entry("segment", "ops"),
    ]
    eff = intersect_scopes(user_scope, agent_scope)
    assert _allow_keys(eff) == {("ontology_module", "finance", "view")}


def test_dormant_agent_c_disjoint_intersection_is_empty() -> None:
    """(c) user_perms ∩ agent_perms = ∅ → deny-all."""
    user_scope = [_entry("ontology_module", "finance")]
    agent_scope = [_entry("ontology_module", "legal")]
    eff = intersect_scopes(user_scope, agent_scope)
    assert _allow_keys(eff) == set()
    assert eff.is_empty() is True


def test_dormant_agent_d_private_to_self_short_circuits_before_agent_scope() -> None:
    """(d) ``private_to_self`` short-circuits BEFORE consulting the agent scope.

    The strict short-circuit lives at the resolver layer; this test pins
    the contract that ``is_strict_visibility("private_to_self")`` is
    truthy and that the other on-row visibility modes are not.
    """
    assert is_strict_visibility("private_to_self") is True
    # Other modes do not short-circuit; intersection logic continues.
    assert is_strict_visibility("scoped_to_role_cluster") is False
    assert is_strict_visibility("permission_matrix_default") is False
    assert is_strict_visibility("private_to_named_list") is False
