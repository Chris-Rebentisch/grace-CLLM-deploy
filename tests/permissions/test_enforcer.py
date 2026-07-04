"""Unit tests for ``src.permissions.enforcer`` (Chunk 42, CP5, D334)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.permissions.enforcer import (
    Enforcer,
    get_enforcer,
    rebuild_enforcer,
)
from src.permissions.models import (
    AccessRule,
    Allow,
    Deny,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
)
from src.permissions.principal_context import (
    ScopeEntry,
    User,
    UserActingViaAgent,
)


def _matrix(**kwargs) -> PermissionMatrix:
    defaults = {"role_clusters": [], "default_decision": "deny"}
    defaults.update(kwargs)
    return PermissionMatrix(**defaults)


def _cluster(person_id: str, *, allow=None, deny=None) -> RoleCluster:
    rules = []
    for kind, label, action in allow or []:
        rules.append(
            AccessRule(
                resource_kind=kind,
                resource_label=label,
                action=action,
                decision="allow",
            )
        )
    for kind, label, action in deny or []:
        rules.append(
            AccessRule(
                resource_kind=kind,
                resource_label=label,
                action=action,
                decision="deny",
            )
        )
    return RoleCluster(
        cluster_id=f"c-{person_id[:8]}",
        display_name=f"Cluster for {person_id[:8]}",
        members=[RoleClusterMember(person_grace_id=person_id)],
        access_rules=rules,
    )


def test_no_active_matrix_returns_deny() -> None:
    enf = Enforcer(matrix=None)
    user = User(user_id=uuid4())
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "no_active_matrix"


def test_explicit_allow_path() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("ontology_module", "finance", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Allow)


def test_explicit_deny_overrides_allow() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(
                str(uid),
                allow=[("ontology_module", "finance", "view")],
                deny=[("ontology_module", "finance", "view")],
            )
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "explicit_deny"


def test_no_matching_rule_falls_to_default_deny() -> None:
    uid = uuid4()
    matrix = _matrix(role_clusters=[_cluster(str(uid))])
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "default_deny"


def test_no_matching_rule_falls_to_default_allow_when_configured() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[_cluster(str(uid))], default_decision="allow"
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Allow)


def test_unrecognized_user_default_denies() -> None:
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uuid4()), allow=[("ontology_module", "finance", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    other_user = User(user_id=uuid4())
    decision = enf.enforce(other_user, "ontology_module", "finance", "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "default_deny"


def test_user_via_agent_with_empty_intersection_denies() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("ontology_module", "finance", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    # Agent has nothing in scope → intersection empty.
    via_agent = UserActingViaAgent(
        user_id=uid,
        agent_id="agent-1",
        user_scope=[
            ScopeEntry(
                resource_kind="ontology_module",
                resource_label="finance",
                action="view",
            )
        ],
        agent_scope=[],
    )
    decision = enf.enforce(via_agent, "ontology_module", "finance", "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "scope_intersection_empty"


def test_rebuild_hook_swaps_matrix_atomically() -> None:
    uid = uuid4()
    matrix1 = _matrix()
    matrix2 = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("ontology_module", "finance", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix1)
    user = User(user_id=uid)

    decision1 = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision1, Deny)

    enf.rebuild_with(matrix2)
    decision2 = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision2, Allow)


def test_module_registry_rebuild() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("ontology_module", "finance", "view")])
        ]
    )
    rebuild_enforcer(matrix)
    enf = get_enforcer()
    user = User(user_id=uid)
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Allow)
    # Reset to None so subsequent tests are not contaminated.
    rebuild_enforcer(None)


def test_user_via_agent_in_scope_allows() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("ontology_module", "finance", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    triple = ScopeEntry(
        resource_kind="ontology_module",
        resource_label="finance",
        action="view",
    )
    via_agent = UserActingViaAgent(
        user_id=uid,
        agent_id="agent-1",
        user_scope=[triple],
        agent_scope=[triple],
    )
    decision = enf.enforce(via_agent, "ontology_module", "finance", "view")
    assert isinstance(decision, Allow)


# ---------------------------------------------------------------------
# F-031 / ISS-0013 — tiered resource_label matching (exact > class-level
# > wildcard). Under default-deny, class-level and wildcard rules make
# per-entity resources (retrieval post-filter grace_ids) ratifiably
# allowable; exact-id rules override class-level, class-level overrides
# wildcard, and deny remains final within the winning tier.
# ---------------------------------------------------------------------


def test_class_level_rule_allows_arbitrary_grace_ids_under_default_deny() -> None:
    """A rule whose resource_label equals the resource KIND matches any
    instance id — the retrieval post-filter's per-entity enforce() calls
    become allowable under default-deny."""
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("graph_entity", "graph_entity", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    for grace_id in (str(uuid4()), str(uuid4()), "entity-abc"):
        decision = enf.enforce(user, "graph_entity", grace_id, "view")
        assert isinstance(decision, Allow)


def test_wildcard_rule_allows_under_default_deny() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[_cluster(str(uid), allow=[("graph_entity", "*", "view")])]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "graph_entity", str(uuid4()), "view")
    assert isinstance(decision, Allow)


def test_class_level_rule_does_not_cross_kinds_or_actions() -> None:
    """Class-level match is scoped to its own resource_kind + action."""
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uid), allow=[("graph_entity", "graph_entity", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    # Different kind → default-deny.
    decision = enf.enforce(user, "ontology_module", "finance", "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "default_deny"
    # Same kind, different action → default-deny.
    decision = enf.enforce(user, "graph_entity", str(uuid4()), "edit")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "default_deny"


def test_exact_id_deny_overrides_class_level_allow() -> None:
    uid = uuid4()
    denied_id = str(uuid4())
    matrix = _matrix(
        role_clusters=[
            _cluster(
                str(uid),
                allow=[("graph_entity", "graph_entity", "view")],
                deny=[("graph_entity", denied_id, "view")],
            )
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    # The exact-id deny wins for that id...
    decision = enf.enforce(user, "graph_entity", denied_id, "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "explicit_deny"
    # ...while any other id is still admitted by the class-level allow.
    decision = enf.enforce(user, "graph_entity", str(uuid4()), "view")
    assert isinstance(decision, Allow)


def test_exact_id_allow_overrides_class_level_deny() -> None:
    uid = uuid4()
    allowed_id = str(uuid4())
    matrix = _matrix(
        role_clusters=[
            _cluster(
                str(uid),
                allow=[("graph_entity", allowed_id, "view")],
                deny=[("graph_entity", "graph_entity", "view")],
            )
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    # More-specific exact allow beats the class-level deny for that id...
    decision = enf.enforce(user, "graph_entity", allowed_id, "view")
    assert isinstance(decision, Allow)
    # ...and every other id stays explicitly denied at class level.
    decision = enf.enforce(user, "graph_entity", str(uuid4()), "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "explicit_deny"


def test_class_level_deny_overrides_wildcard_allow() -> None:
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(
                str(uid),
                allow=[("graph_entity", "*", "view")],
                deny=[("graph_entity", "graph_entity", "view")],
            )
        ]
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "graph_entity", str(uuid4()), "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "explicit_deny"


def test_deny_wins_within_same_tier() -> None:
    """Deny-bias is preserved inside a specificity tier (D334)."""
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(
                str(uid),
                allow=[("graph_entity", "graph_entity", "view")],
                deny=[("graph_entity", "graph_entity", "view")],
            )
        ],
        default_decision="allow",
    )
    enf = Enforcer(matrix=matrix)
    user = User(user_id=uid)
    decision = enf.enforce(user, "graph_entity", str(uuid4()), "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "explicit_deny"


def test_class_level_and_wildcard_do_not_match_for_nonmembers() -> None:
    """Tiered matching only widens rules a principal actually HOLDS —
    a non-member still falls to default-deny."""
    uid = uuid4()
    matrix = _matrix(
        role_clusters=[
            _cluster(str(uuid4()), allow=[("graph_entity", "*", "view")])
        ]
    )
    enf = Enforcer(matrix=matrix)
    outsider = User(user_id=uid)
    decision = enf.enforce(outsider, "graph_entity", str(uuid4()), "view")
    assert isinstance(decision, Deny)
    assert decision.reason.code == "default_deny"
