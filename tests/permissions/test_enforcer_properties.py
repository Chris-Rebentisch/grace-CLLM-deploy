"""Hypothesis property-based tests for the Enforcer (Chunk 42, CP5, D334).

Pins the two structural invariants of the enforcer:

* **Deny-bias** — for any matrix that contains an explicit deny on the
  triple under test for any of the principal's clusters, the enforcer
  always returns ``Deny``, regardless of how many other allows exist.
* **No escalation** — for a ``UserActingViaAgent`` whose effective
  scope (user ∩ agent) does not admit the requested triple, the
  enforcer never returns ``Allow``. This is the dormant-agent
  forward-guarantee for Chunk 44.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from hypothesis import given, settings, strategies as st

from src.permissions.enforcer import Enforcer
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


_RESOURCE_KINDS = ["ontology_module", "segment", "change_directive"]
_ACTIONS = ["view", "edit", "ratify"]
_LABELS = ["finance", "legal", "ops", "hr"]


_decision_strategy = st.sampled_from(["allow", "deny"])
_kind_strategy = st.sampled_from(_RESOURCE_KINDS)
_label_strategy = st.sampled_from(_LABELS)
_action_strategy = st.sampled_from(_ACTIONS)


@st.composite
def _rule_strategy(draw) -> AccessRule:
    return AccessRule(
        resource_kind=draw(_kind_strategy),
        resource_label=draw(_label_strategy),
        action=draw(_action_strategy),
        decision=draw(_decision_strategy),
    )


@st.composite
def _cluster_strategy(draw, person_id: str) -> RoleCluster:
    n_rules = draw(st.integers(min_value=0, max_value=8))
    rules = [draw(_rule_strategy()) for _ in range(n_rules)]
    return RoleCluster(
        cluster_id=f"c-{person_id[:8]}",
        display_name="cluster",
        members=[RoleClusterMember(person_grace_id=person_id)],
        access_rules=rules,
    )


@settings(max_examples=80, deadline=None)
@given(
    rules=st.lists(_rule_strategy(), min_size=0, max_size=10),
    target_kind=_kind_strategy,
    target_label=_label_strategy,
    target_action=_action_strategy,
)
def test_deny_bias_invariant(
    rules: list[AccessRule],
    target_kind: str,
    target_label: str,
    target_action: str,
) -> None:
    """If any matched cluster has a deny rule for the target triple,
    the enforcer must return Deny."""
    uid = uuid4()
    user = User(user_id=uid)
    cluster = RoleCluster(
        cluster_id="c-1",
        display_name="c",
        members=[RoleClusterMember(person_grace_id=str(uid))],
        access_rules=rules,
    )
    matrix = PermissionMatrix(
        role_clusters=[cluster], default_decision="allow"
    )
    enf = Enforcer(matrix=matrix)

    has_explicit_deny = any(
        r.resource_kind == target_kind
        and r.resource_label == target_label
        and r.action == target_action
        and r.decision == "deny"
        for r in rules
    )

    decision = enf.enforce(user, target_kind, target_label, target_action)

    if has_explicit_deny:
        assert isinstance(decision, Deny)


@settings(max_examples=60, deadline=None)
@given(
    rules=st.lists(_rule_strategy(), min_size=1, max_size=8),
    user_scope_kind=_kind_strategy,
    user_scope_label=_label_strategy,
    user_scope_action=_action_strategy,
    agent_scope_kind=_kind_strategy,
    agent_scope_label=_label_strategy,
    agent_scope_action=_action_strategy,
    target_kind=_kind_strategy,
    target_label=_label_strategy,
    target_action=_action_strategy,
)
def test_no_escalation_invariant_for_user_via_agent(
    rules: list[AccessRule],
    user_scope_kind: str,
    user_scope_label: str,
    user_scope_action: str,
    agent_scope_kind: str,
    agent_scope_label: str,
    agent_scope_action: str,
    target_kind: str,
    target_label: str,
    target_action: str,
) -> None:
    """For ``UserActingViaAgent``, if the (kind, label, action) triple
    is not in (user_scope ∩ agent_scope), the enforcer cannot return
    Allow.

    This pins the agent-intersection constraint for Chunk 44 — an agent
    NEVER escalates a user's permissions; even when the matrix grants,
    the intersection must admit.
    """
    uid = uuid4()
    user_scope_entry = ScopeEntry(
        resource_kind=user_scope_kind,
        resource_label=user_scope_label,
        action=user_scope_action,
    )
    agent_scope_entry = ScopeEntry(
        resource_kind=agent_scope_kind,
        resource_label=agent_scope_label,
        action=agent_scope_action,
    )
    via_agent = UserActingViaAgent(
        user_id=uid,
        agent_id="a-1",
        user_scope=[user_scope_entry],
        agent_scope=[agent_scope_entry],
    )
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="c-1",
                display_name="c",
                members=[RoleClusterMember(person_grace_id=str(uid))],
                access_rules=rules,
            )
        ],
        default_decision="deny",
    )
    enf = Enforcer(matrix=matrix)

    intersection_admits = (
        user_scope_kind == agent_scope_kind
        and user_scope_label == agent_scope_label
        and user_scope_action == agent_scope_action
        and target_kind == user_scope_kind
        and target_label == user_scope_label
        and target_action == user_scope_action
    )

    decision = enf.enforce(via_agent, target_kind, target_label, target_action)

    if not intersection_admits and isinstance(decision, Allow):
        # When the intersection does not admit and the matrix grants,
        # the enforcer must still deny on the agent constraint.
        # If we reached an Allow here, the no-escalation invariant is broken.
        raise AssertionError(
            f"no_escalation invariant violated: matrix granted but "
            f"intersection did not admit (target={target_kind}/{target_label}/"
            f"{target_action}, user_scope={user_scope_entry}, "
            f"agent_scope={agent_scope_entry})"
        )


@settings(max_examples=50, deadline=None)
@given(
    rules=st.lists(_rule_strategy(), min_size=0, max_size=6),
    target_kind=_kind_strategy,
    target_label=_label_strategy,
    target_action=_action_strategy,
)
def test_decision_is_well_formed(
    rules: list[AccessRule],
    target_kind: str,
    target_label: str,
    target_action: str,
) -> None:
    """Every enforce() call returns either an Allow or a Deny+Reason."""
    uid = uuid4()
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="c-1",
                display_name="c",
                members=[RoleClusterMember(person_grace_id=str(uid))],
                access_rules=rules,
            )
        ],
        default_decision="deny",
    )
    enf = Enforcer(matrix=matrix)
    decision = enf.enforce(User(user_id=uid), target_kind, target_label, target_action)
    assert isinstance(decision, (Allow, Deny))
    if isinstance(decision, Deny):
        assert decision.reason is not None


@settings(max_examples=30, deadline=None)
@given(
    matrix_default=st.sampled_from(["allow", "deny"]),
    target_kind=_kind_strategy,
    target_label=_label_strategy,
    target_action=_action_strategy,
)
def test_unknown_user_falls_to_default(
    matrix_default: str,
    target_kind: str,
    target_label: str,
    target_action: str,
) -> None:
    """A principal whose ``user_id`` matches no member of any role-cluster
    receives the matrix's default decision."""
    matrix = PermissionMatrix(
        role_clusters=[],
        default_decision=matrix_default,
    )
    enf = Enforcer(matrix=matrix)
    decision = enf.enforce(
        User(user_id=uuid4()), target_kind, target_label, target_action
    )
    if matrix_default == "allow":
        assert isinstance(decision, Allow)
    else:
        assert isinstance(decision, Deny)


@settings(max_examples=30, deadline=None)
@given(
    target_kind=_kind_strategy,
    target_label=_label_strategy,
    target_action=_action_strategy,
)
def test_no_matrix_always_denies(
    target_kind: str, target_label: str, target_action: str
) -> None:
    enf = Enforcer(matrix=None)
    decision = enf.enforce(
        User(user_id=uuid4()), target_kind, target_label, target_action
    )
    assert isinstance(decision, Deny)
    assert decision.reason.code == "no_active_matrix"
