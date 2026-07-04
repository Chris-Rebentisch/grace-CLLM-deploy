"""Shared helper: derive a principal's forbidden sensitivity tags from the
active permission matrix (D521, §8.2).

Used by both the cypher rewriter (CP4) and post-fetch enforce (CP5) to
avoid duplicating the derivation logic.

D356 capture-the-why: D521 — extends D270 single-engine coverage
(no new engine) with domain-entity sensitivity filtering; derivation
algorithm per spec §8.2.
"""

from __future__ import annotations

import structlog

from src.permissions.principal_context import PrincipalContext

logger = structlog.get_logger()

# D426 closed vocabulary — tags outside this set are ignored for enforcement
# (operator-defined extension tags do not cause false denials).
D426_VOCABULARY: frozenset[str] = frozenset({
    "privileged",
    "pii_dense",
    "external_boundary",
    "privilege_potentially_waived",
})


def _clusters_for_principal(principal: PrincipalContext, matrix: object) -> list:
    """Return the role-clusters the principal belongs to by membership.

    F-47: mirrors ``Enforcer._find_clusters_for`` — a principal is a member of
    a cluster when ``member.person_grace_id`` equals the principal's ``user_id``.
    Returns an empty list for an anonymous principal (``user_id is None``).
    """
    user_id = getattr(principal, "user_id", None)
    if user_id is None:
        return []
    user_id_str = str(user_id)
    matched = []
    for cluster in matrix.role_clusters:
        for member in cluster.members:
            if str(member.person_grace_id) == user_id_str:
                matched.append(cluster)
                break
    return matched


def _cluster_visible_tags(cluster: object) -> set[str]:
    """Union of ``SensitivityTag.name`` from the cluster's own tags and its
    per-rule tags (spec §8.2 step 3, scoped to a single cluster)."""
    visible = {tag.name for tag in cluster.sensitivity_tags}
    for rule in cluster.access_rules:
        visible.update(tag.name for tag in rule.sensitivity_tags)
    return visible


def _forbidden_for_visible(visible_tags: set[str]) -> set[str]:
    """Spec §8.2 steps 5–6 for one visible-tag set, including the
    empty-visible edge case (no tags configured -> no restrictions)."""
    if not visible_tags:
        return set()
    return D426_VOCABULARY - (visible_tags & D426_VOCABULARY)


def resolve_forbidden_tags(
    principal: PrincipalContext,
    matrix: object | None = None,
) -> set[str]:
    """Derive the set of D426 tags the principal may NOT see.

    Algorithm (spec §8.2):
    1. Load the active permission matrix.
    2. Resolve the principal's role-clusters.
    3. Collect SensitivityTag.name from both AccessRule.sensitivity_tags
       and RoleCluster.sensitivity_tags across all matched clusters.
    4. Union = visible_tags.
    5. Intersect with D426_VOCABULARY.
    6. Forbidden = D426_VOCABULARY - intersected visible set.

    Edge cases:
    - No matrix active -> empty forbidden set.
    - Principal has no cluster matches (anonymous/unresolvable) -> MOST
      restrictive posture: union of every cluster's forbidden set
      (F-031 / ISS-0013 — never more permissive than any named principal).
    - Visible-tag union is empty after matching -> no restrictions
      (operators haven't configured tag-level access).

    Returns:
        Set of forbidden tag names (subset of D426_VOCABULARY).
    """
    if matrix is None:
        return set()

    # Import PermissionMatrix type for duck-typing check
    from src.permissions.models import PermissionMatrix

    if not isinstance(matrix, PermissionMatrix):
        return set()

    if not matrix.role_clusters:
        return set()

    # F-47 (validation run, 2026-07-01) capture-the-why: v1 unioned the
    # visible tags across ALL clusters, so sensitivity access was matrix-global —
    # a reviewer's and a restricted user's forbidden set were identical and
    # per-principal zones (the entire point of the matrix) did not exist.
    #
    # This resolves the principal to the clusters they actually belong to
    # (member.person_grace_id == principal.user_id, mirroring
    # Enforcer._find_clusters_for) and derives visible tags from THOSE clusters.
    principal_clusters = _clusters_for_principal(principal, matrix)

    if not principal_clusters:
        # F-031 / ISS-0013 (documented F-47 half): the v1 fallback handed an
        # unresolvable/anonymous principal (user_id=None, or a user_id that
        # matches no cluster) the matrix-global UNION of visible tags — the
        # MOST permissive posture — so an anonymous caller saw privileged
        # content that a named restricted principal could not. An unknown
        # identity must resolve to the MOST restrictive posture instead:
        # forbidden = union of every cluster's forbidden set (anonymous sees
        # the least; never more than any named principal).
        forbidden: set[str] = set()
        for cluster in matrix.role_clusters:
            forbidden |= _forbidden_for_visible(_cluster_visible_tags(cluster))
        return forbidden

    visible_tags: set[str] = set()
    for cluster in principal_clusters:
        # Cluster-level tags (models.py:347) + per-rule tags (models.py:323)
        visible_tags |= _cluster_visible_tags(cluster)

    # §8.2 edge case handled inside the helper: visible-tag union empty after
    # matching -> "no sensitivity restrictions" (no predicate injected).
    # Otherwise forbidden = D426 vocabulary minus the visible-in-vocab set.
    return _forbidden_for_visible(visible_tags)


def resolve_enforcement_posture(
    principal: PrincipalContext,
    matrix: object | None = None,
) -> str:
    """Resolve the CP5 enforcement posture for forbidden-tagged vertices.

    F-0047b / ISS-0055 Layer 2 (2026-07-03) — capture-the-why: the matrix's
    ``inherited_tag_posture`` knob is only honored for a principal that
    RESOLVES to at least one role-cluster. Anonymous / unresolvable
    principals always get ``"deny"`` (drop-on-forbidden-tag) regardless of
    the knob — this preserves the F-031 / ISS-0013 most-restrictive
    fallback discipline: an unknown identity must never see more than any
    named principal, and evidence-scoped partial serving reveals vertex
    EXISTENCE that "deny" hides.

    Fail-safe: any condition under which the posture cannot be positively
    resolved (no matrix, wrong type, no clusters, missing/unknown knob
    value) returns ``"deny"``.

    Returns:
        ``"deny"`` or ``"evidence_scoped"``.
    """
    if matrix is None:
        return "deny"

    from src.permissions.models import PermissionMatrix

    if not isinstance(matrix, PermissionMatrix):
        return "deny"
    if getattr(matrix, "inherited_tag_posture", "deny") != "evidence_scoped":
        return "deny"
    if not matrix.role_clusters:
        return "deny"
    if not _clusters_for_principal(principal, matrix):
        # Anonymous / unresolvable -> most-restrictive posture always.
        return "deny"
    return "evidence_scoped"
