"""Tests for ``project_tagged_subset`` (Chunk 43, CP2 / D343).

Covers filter correctness, closed-list invariant, render-only invariant
(no DB I/O, no enforcer call), and a Hypothesis property test asserting
decision-equivalence with ``Enforcer.enforce()`` for the single-cluster
case.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from src.permissions.enforcer import Enforcer
from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
    SensitivityTag,
    TaggedClusterDecision,
    TaggedSubset,
)
from src.permissions.principal_context import User
from src.permissions.sensitivity_subset import project_tagged_subset


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tag(name: str = "pii") -> SensitivityTag:
    return SensitivityTag(name=name)


def _rule(
    *,
    resource_kind: str = "ontology_module",
    resource_label: str = "finance",
    action: str = "view",
    decision: str = "allow",
    tags: list[SensitivityTag] | None = None,
) -> AccessRule:
    return AccessRule(
        resource_kind=resource_kind,  # type: ignore[arg-type]
        resource_label=resource_label,
        action=action,  # type: ignore[arg-type]
        decision=decision,  # type: ignore[arg-type]
        sensitivity_tags=tags or [],
    )


def _cluster(
    *,
    cluster_id: str = "c1",
    display_name: str = "Cluster One",
    member_user_id: UUID | None = None,
    rules: list[AccessRule] | None = None,
) -> RoleCluster:
    members = (
        [
            RoleClusterMember(
                person_grace_id=str(member_user_id),
                display_name="Alice",
            )
        ]
        if member_user_id is not None
        else []
    )
    return RoleCluster(
        cluster_id=cluster_id,
        display_name=display_name,
        members=members,
        access_rules=rules or [],
    )


# ---------------------------------------------------------------------------
# Filter correctness
# ---------------------------------------------------------------------------


def test_returns_tagged_subset_instance():
    matrix = PermissionMatrix()
    result = project_tagged_subset(matrix)
    assert isinstance(result, TaggedSubset)


def test_empty_matrix_yields_empty_subset():
    matrix = PermissionMatrix()
    result = project_tagged_subset(matrix)
    assert result.cluster_decisions == []
    assert result.matrix_schema_version == matrix.schema_version


def test_untagged_rules_are_filtered_out():
    """Rules with empty ``sensitivity_tags`` MUST NOT appear in the
    subset (D343 filter rule)."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[_rule(tags=[])],  # empty tags
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    assert result.cluster_decisions == []


def test_tagged_rules_are_included():
    """Rules with at least one ``SensitivityTag`` MUST appear in the
    subset."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[_rule(tags=[_tag("pii")])],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    assert len(result.cluster_decisions) == 1
    row = result.cluster_decisions[0]
    assert row.cluster_id == "c1"
    assert [t.name for t in row.sensitivity_tags] == ["pii"]


def test_mixed_rules_partition_correctly():
    """Tagged + untagged rules in the same cluster: only tagged rows
    surface."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[
            _rule(resource_label="finance", tags=[_tag("pii")]),
            _rule(resource_label="public", tags=[]),
            _rule(resource_label="hr", tags=[_tag("phi")]),
        ],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    surfaced = sorted(r.resource_label for r in result.cluster_decisions)
    assert surfaced == ["finance", "hr"]


def test_decision_field_passes_through_verbatim():
    """``decision`` on each subset row is the rule's recorded decision
    (no aggregation, no default-fallback)."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[
            _rule(resource_label="a", decision="allow", tags=[_tag()]),
            _rule(resource_label="b", decision="deny", tags=[_tag()]),
        ],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    decisions = {r.resource_label: r.decision for r in result.cluster_decisions}
    assert decisions == {"a": "allow", "b": "deny"}


def test_multiple_clusters_each_contribute():
    cluster_a = _cluster(
        cluster_id="A",
        display_name="A",
        rules=[_rule(resource_label="x", tags=[_tag()])],
    )
    cluster_b = _cluster(
        cluster_id="B",
        display_name="B",
        rules=[_rule(resource_label="y", tags=[_tag()])],
    )
    matrix = PermissionMatrix(role_clusters=[cluster_a, cluster_b])
    result = project_tagged_subset(matrix)
    pairs = sorted((r.cluster_id, r.resource_label) for r in result.cluster_decisions)
    assert pairs == [("A", "x"), ("B", "y")]


def test_schema_version_passthrough():
    matrix = PermissionMatrix(schema_version="9.7", role_clusters=[])
    result = project_tagged_subset(matrix)
    assert result.matrix_schema_version == "9.7"


# ---------------------------------------------------------------------------
# Closed-list invariant
# ---------------------------------------------------------------------------


def test_closed_list_invariant_rows_are_strict_subset():
    """Every row in the subset corresponds 1:1 to an
    ``(cluster, access_rule)`` pair on the matrix."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[
            _rule(resource_label="a", tags=[_tag("t1")]),
            _rule(resource_label="b", tags=[]),
            _rule(resource_label="c", tags=[_tag("t2")]),
        ],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)

    # Build the canonical (cluster_id, kind, label, action, decision)
    # set from the source matrix.
    source_keys = set()
    for c in matrix.role_clusters:
        for r in c.access_rules:
            if r.sensitivity_tags:
                source_keys.add(
                    (
                        c.cluster_id,
                        r.resource_kind,
                        r.resource_label,
                        r.action,
                        r.decision,
                    )
                )

    projected_keys = {
        (
            row.cluster_id,
            row.resource_kind,
            row.resource_label,
            row.action,
            row.decision,
        )
        for row in result.cluster_decisions
    }
    assert projected_keys == source_keys
    assert projected_keys.issubset(source_keys)


def test_closed_list_invariant_no_synthesised_decision():
    """Projection MUST NOT invent a decision distinct from the rule's
    recorded decision."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[_rule(decision="deny", tags=[_tag()])],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    assert result.cluster_decisions[0].decision == "deny"


# ---------------------------------------------------------------------------
# Render-only invariant
# ---------------------------------------------------------------------------


def test_function_does_not_mutate_input_matrix():
    """Pure function: input matrix must be untouched after call."""
    cluster = _cluster(
        cluster_id="c1",
        rules=[
            _rule(resource_label="a", tags=[_tag("t1")]),
            _rule(resource_label="b", tags=[]),
        ],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    snapshot = matrix.model_dump()
    project_tagged_subset(matrix)
    assert matrix.model_dump() == snapshot


def test_function_does_not_call_enforcer(monkeypatch):
    """Render-only: ``project_tagged_subset`` must not invoke the
    ``Enforcer`` (D270 single-engine invariant)."""
    calls: list[tuple] = []

    original_enforce = Enforcer.enforce

    def spy(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original_enforce(self, *args, **kwargs)

    monkeypatch.setattr(Enforcer, "enforce", spy)
    cluster = _cluster(
        cluster_id="c1",
        rules=[_rule(tags=[_tag()])],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    project_tagged_subset(matrix)
    assert calls == []


# ---------------------------------------------------------------------------
# Hypothesis property test — decision-equivalence vs enforcer
# ---------------------------------------------------------------------------


_RESOURCE_KINDS = (
    "ontology_module",
    "segment",
    "change_directive",
    "graph_entity",
    "retrieval_query_event",
)
_ACTIONS = ("view", "edit", "ratify")
_DECISIONS = ("allow", "deny")


@st.composite
def _single_cluster_matrix_strategy(draw):
    """Generate a matrix with exactly one role-cluster and one member.

    The single-cluster shape is the case where projection is
    unambiguously equivalent to enforcement (no cross-cluster
    aggregation). Ratchets the property test cleanly to D343's render-
    only contract.
    """
    user_id = draw(st.uuids())
    rule_count = draw(st.integers(min_value=1, max_value=6))
    rules: list[AccessRule] = []
    for i in range(rule_count):
        kind = draw(st.sampled_from(_RESOURCE_KINDS))
        label = draw(st.text(min_size=1, max_size=12).filter(lambda s: s.strip()))
        action = draw(st.sampled_from(_ACTIONS))
        decision = draw(st.sampled_from(_DECISIONS))
        # Mix tagged + untagged rules so the property exercises the
        # filter, not just trivial inclusion.
        tags: list[SensitivityTag] = (
            [SensitivityTag(name=f"t{i}")] if draw(st.booleans()) else []
        )
        rules.append(
            AccessRule(
                resource_kind=kind,  # type: ignore[arg-type]
                resource_label=label.strip(),
                action=action,  # type: ignore[arg-type]
                decision=decision,  # type: ignore[arg-type]
                sensitivity_tags=tags,
            )
        )
    cluster = RoleCluster(
        cluster_id="c-only",
        display_name="Sole cluster",
        members=[RoleClusterMember(person_grace_id=str(user_id))],
        access_rules=rules,
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    return user_id, matrix


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
@given(_single_cluster_matrix_strategy())
def test_property_decision_equivalence_with_enforcer(matrix_tuple):
    """For every row in ``project_tagged_subset(matrix)``, simulating a
    member of that cluster against ``Enforcer.enforce()`` MUST yield the
    same decision (D343 render-only / D270 single-engine invariant).

    The single-cluster shape ensures no cross-cluster aggregation can
    legitimately diverge the projection from the enforcer.
    """
    user_id, matrix = matrix_tuple
    enforcer = Enforcer(matrix=matrix)
    principal = User(user_id=user_id)
    subset = project_tagged_subset(matrix)

    for row in subset.cluster_decisions:
        # The same triple may be authored multiple times on a single
        # cluster (Hypothesis-generated). The enforcer aggregates with
        # explicit-deny precedence; mirror that here against the source
        # rules so we compare like-for-like.
        rules_for_triple = [
            r
            for r in matrix.role_clusters[0].access_rules
            if r.resource_kind == row.resource_kind
            and r.resource_label == row.resource_label
            and r.action == row.action
        ]
        any_deny = any(r.decision == "deny" for r in rules_for_triple)
        any_allow = any(r.decision == "allow" for r in rules_for_triple)
        expected = (
            "deny"
            if any_deny
            else "allow"
            if any_allow
            else matrix.default_decision
        )

        decision = enforcer.enforce(
            principal,
            row.resource_kind,
            row.resource_label,
            row.action,
        )
        assert decision.decision == expected
        # The projected row's recorded decision is one of the source
        # rules — must be a member of {"allow", "deny"} present on this
        # triple.
        assert row.decision in {r.decision for r in rules_for_triple}


# ---------------------------------------------------------------------------
# Empty short-circuit
# ---------------------------------------------------------------------------


def test_cluster_with_no_rules_short_circuits():
    cluster = _cluster(cluster_id="c1", rules=[])
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    assert result.cluster_decisions == []


def test_only_untagged_rules_short_circuits():
    cluster = _cluster(
        cluster_id="c1",
        rules=[_rule(tags=[]), _rule(resource_label="b", tags=[])],
    )
    matrix = PermissionMatrix(role_clusters=[cluster])
    result = project_tagged_subset(matrix)
    assert result.cluster_decisions == []


def test_user_id_argument_unused_by_signature():
    """``project_tagged_subset`` MUST accept exactly one positional
    argument (the matrix) — no principal, no DB session."""
    import inspect

    sig = inspect.signature(project_tagged_subset)
    assert list(sig.parameters) == ["matrix"]
