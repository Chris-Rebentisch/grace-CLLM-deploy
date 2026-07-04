"""Render-only tagged-subset projection (Chunk 43, CP2 / D343).

``project_tagged_subset(matrix)`` filters the active
``PermissionMatrix``'s cluster decisions to those whose
``AccessRule.sensitivity_tags`` is non-empty and returns a
``TaggedSubset`` for render only.

Hard invariants (D270 / D343 — load-bearing):

* No DB I/O. Pure function over the in-memory matrix object.
* No admission logic. The Sensitivity Gate does not decide; it merely
  re-renders rules already authored on the Chunk 42 matrix.
* No import of ``src.permissions.enforcer``. Enforced by the AST guard
  in ``tests/permissions/test_d270_invariant.py``.
* Closed-list output. Every row in the result is byte-faithful to a row
  already on ``matrix.role_clusters[*].access_rules``.

The Hypothesis property test in
``tests/permissions/test_sensitivity_subset.py`` proves
decision-equivalence with ``Enforcer.enforce()`` for the single-cluster
case (the case where projection is unambiguous).
"""

from __future__ import annotations

from src.permissions.models import (
    PermissionMatrix,
    TaggedClusterDecision,
    TaggedSubset,
)


def project_tagged_subset(matrix: PermissionMatrix) -> TaggedSubset:
    """Filter ``matrix`` to a render-only ``TaggedSubset`` of cluster
    decisions carrying at least one ``SensitivityTag``.

    Args:
        matrix: The active ``PermissionMatrix``. Treated as immutable;
            this function does not mutate the input.

    Returns:
        A ``TaggedSubset`` whose ``cluster_decisions`` is a closed-list
        subset of the input matrix's ``(cluster, access_rule)`` pairs.

    Notes:
        Filter rule: include a row iff
        ``rule.sensitivity_tags`` is non-empty (D343).

        ``decision`` on each row is the rule's recorded decision verbatim
        — no aggregation across clusters, no agent-scope intersection,
        no ``default_decision`` fallback. Aggregation belongs to the
        enforcer (D270 single-engine invariant).
    """
    decisions: list[TaggedClusterDecision] = []
    for cluster in matrix.role_clusters:
        for rule in cluster.access_rules:
            if not rule.sensitivity_tags:
                continue
            decisions.append(
                TaggedClusterDecision(
                    cluster_id=cluster.cluster_id,
                    cluster_display_name=cluster.display_name,
                    resource_kind=rule.resource_kind,
                    resource_label=rule.resource_label,
                    action=rule.action,
                    decision=rule.decision,
                    sensitivity_tags=list(rule.sensitivity_tags),
                )
            )
    return TaggedSubset(
        matrix_schema_version=matrix.schema_version,
        cluster_decisions=decisions,
    )


__all__ = ["project_tagged_subset"]
