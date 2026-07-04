"""F-47 regression: resolve_forbidden_tags must be principal-aware.

Before the fix, visible tags were unioned across ALL clusters (matrix-global),
so a reviewer and a restricted user had identical forbidden sets — per-principal
zones did not exist. The fix resolves the principal to their member clusters and
derives visibility from those.

F-031 / ISS-0013 (documented F-47 half): an anonymous/unresolvable principal
previously fell back to the matrix-global visible-tag UNION — the MOST
permissive posture — so an anonymous caller saw privileged content a named
restricted principal could not. It now resolves to the MOST restrictive
posture: forbidden = union of every cluster's forbidden set.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from src.permissions.models import (
    AccessRule,
    PermissionMatrix,
    RoleCluster,
    RoleClusterMember,
    SensitivityTag,
)
from src.permissions.principal_context import User
from src.permissions.sensitivity_resolver import (
    D426_VOCABULARY,
    resolve_forbidden_tags,
)


def _cluster(cluster_id, member_id, visible_tags):
    return RoleCluster(
        cluster_id=cluster_id,
        display_name=cluster_id,
        # person_grace_id is a STRING column; principals carry a UUID user_id —
        # matching is str(member.person_grace_id) == str(user_id).
        members=[RoleClusterMember(person_grace_id=str(member_id))],
        access_rules=[
            AccessRule(
                resource_kind="graph_entity",
                resource_label="*",
                action="view",
                decision="allow",
                sensitivity_tags=[SensitivityTag(name=t) for t in visible_tags],
            )
        ],
        sensitivity_tags=[SensitivityTag(name=t) for t in visible_tags],
    )


def _matrix(reviewer_id, restricted_id):
    # Reviewer cluster can see privileged + external_boundary.
    # Restricted cluster can see only external_boundary.
    return PermissionMatrix(
        role_clusters=[
            _cluster("reviewers", reviewer_id, ["privileged", "external_boundary"]),
            _cluster("restricted", restricted_id, ["external_boundary"]),
        ],
        default_decision="allow",
    )


def test_per_principal_zones_differ():
    reviewer_id = uuid4()
    restricted_id = uuid4()
    matrix = _matrix(reviewer_id, restricted_id)

    reviewer_forbidden = resolve_forbidden_tags(
        User(user_id=UUID(str(reviewer_id))), matrix
    )
    restricted_forbidden = resolve_forbidden_tags(
        User(user_id=UUID(str(restricted_id))), matrix
    )

    # Reviewer may see privileged → NOT forbidden.
    assert "privileged" not in reviewer_forbidden
    # Restricted user may NOT see privileged → forbidden.
    assert "privileged" in restricted_forbidden
    # The two principals have genuinely different zones (F-47's whole point).
    assert reviewer_forbidden != restricted_forbidden


def test_anonymous_principal_gets_most_restrictive_posture():
    """F-031 / ISS-0013: user_id=None matches no cluster → forbidden set is
    the UNION of every cluster's forbidden set (most restrictive), never the
    v1 matrix-global visible union (most permissive)."""
    reviewer_id = uuid4()
    restricted_id = uuid4()
    matrix = _matrix(reviewer_id, restricted_id)

    anon_forbidden = resolve_forbidden_tags(User(), matrix)

    # Reviewers forbidden: vocab - {privileged, external_boundary};
    # restricted forbidden: vocab - {external_boundary}. Union of the two:
    assert anon_forbidden == D426_VOCABULARY - {"external_boundary"}
    # In particular, privileged is forbidden for anonymous (the restricted
    # cluster cannot see it, so anonymous must not either).
    assert "privileged" in anon_forbidden


def test_anonymous_never_sees_more_than_any_named_principal():
    """F-031 / ISS-0013: the anonymous forbidden set is a superset of every
    named principal's forbidden set — anonymous can never see something a
    restricted principal cannot."""
    reviewer_id = uuid4()
    restricted_id = uuid4()
    matrix = _matrix(reviewer_id, restricted_id)

    anon_forbidden = resolve_forbidden_tags(User(), matrix)
    reviewer_forbidden = resolve_forbidden_tags(
        User(user_id=UUID(str(reviewer_id))), matrix
    )
    restricted_forbidden = resolve_forbidden_tags(
        User(user_id=UUID(str(restricted_id))), matrix
    )

    assert anon_forbidden >= reviewer_forbidden
    assert anon_forbidden >= restricted_forbidden


def test_unknown_user_id_gets_most_restrictive_posture():
    """A user_id that matches NO cluster is unresolvable — same most-
    restrictive posture as anonymous (F-031 / ISS-0013)."""
    reviewer_id = uuid4()
    restricted_id = uuid4()
    matrix = _matrix(reviewer_id, restricted_id)

    stranger_forbidden = resolve_forbidden_tags(User(user_id=uuid4()), matrix)
    anon_forbidden = resolve_forbidden_tags(User(), matrix)
    assert stranger_forbidden == anon_forbidden == (
        D426_VOCABULARY - {"external_boundary"}
    )


def test_anonymous_with_untagged_clusters_has_no_restrictions():
    """When no cluster configures sensitivity tags at all, every cluster's
    forbidden set is empty (§8.2 empty-visible edge case) — the anonymous
    union is empty too (behavior-compatible with untagged deployments)."""
    matrix = PermissionMatrix(
        role_clusters=[
            RoleCluster(
                cluster_id="untagged",
                display_name="untagged",
                members=[RoleClusterMember(person_grace_id=str(uuid4()))],
                access_rules=[],
            )
        ],
        default_decision="allow",
    )
    assert resolve_forbidden_tags(User(), matrix) == set()
