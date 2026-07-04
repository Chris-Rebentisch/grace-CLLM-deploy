"""In-house permission enforcer (Chunk 42, D334).

The single policy engine per D270. ``Enforcer.enforce(principal,
resource_kind, resource_label, action)`` returns an ``AllowDeny`` typed
union. The enforcer is in-memory; the active matrix is loaded once on
boot and atomically rebound on every ``permission_matrix_ratified``
telemetry event (the ratify route owns the rebuild).

Design principles (D334):

* Default-deny — if the matrix is missing, the enforcer denies with
  ``no_active_matrix``.
* Explicit deny is final — if any matching rule on any of the
  principal's role-clusters denies at the winning specificity tier,
  the result is deny. (F-031 / ISS-0013: labels match at three tiers —
  exact > class-level (label == resource kind) > wildcard ``"*"`` —
  so a ratifiable rule CAN allow per-entity resources under
  default-deny.)
* Explicit allow on a role-cluster grants access, modulo the
  ``UserActingViaAgent`` agent-scope intersection (D338).
* When no rule matches and no deny applies, the matrix's
  ``default_decision`` decides (default ``"deny"``; OWASP A01).

The enforcer never queries the database — Postgres / ArcadeDB lookups
belong to the evidence collector and the API layer. The enforcer reads
the in-memory matrix only. A rebuild on a stale or in-flight matrix is
race-free because the registry is rebound atomically (a single Python
attribute assignment is atomic; readers see either the old or the new
matrix, never a partial state).

Casbin and other off-the-shelf policy engines were rejected (Q1 closed).
The hand-rolled enforcer is small enough to reason about and avoids a
new dependency.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from src.permissions.models import (
    Allow,
    AllowDeny,
    Deny,
    EnforcementReason,
    PermissionMatrix,
    RoleCluster,
)
from src.permissions.principal_context import (
    Action,
    PrincipalContext,
    ResourceKind,
    User,
    UserActingViaAgent,
    effective_scope,
)

logger = structlog.get_logger()

# F-031 / ISS-0013: literal wildcard resource_label. A rule carrying this
# label matches ANY resource_label of its resource_kind (lowest-specificity
# tier — exact and class-level matches take precedence).
WILDCARD_RESOURCE_LABEL = "*"


@dataclass(frozen=True)
class Resource:
    """The (kind, label) pair the enforcer is asked to gate."""

    kind: ResourceKind
    label: str


class Enforcer:
    """In-memory permission enforcer.

    The active ``PermissionMatrix`` is held privately and replaced
    atomically by ``rebuild_with``. ``enforce`` is pure relative to the
    held matrix.
    """

    def __init__(self, matrix: PermissionMatrix | None = None) -> None:
        self._matrix: PermissionMatrix | None = matrix

    @property
    def matrix(self) -> PermissionMatrix | None:
        return self._matrix

    def rebuild_with(self, matrix: PermissionMatrix | None) -> None:
        """Atomically rebind the active matrix.

        Called from the ratify route after a successful
        ``insert_matrix()`` returns. The single attribute assignment
        below is atomic in CPython under the GIL, so concurrent readers
        see either the old or the new matrix — never a partial state.
        """
        self._matrix = matrix

    def enforce(
        self,
        principal: PrincipalContext | User | UserActingViaAgent,
        resource_kind: ResourceKind | str,
        resource_label: str,
        action: Action | str,
    ) -> AllowDeny:
        """Return ``Allow`` or ``Deny + EnforcementReason``.

        Lookup order:

        1. If matrix is ``None`` → deny with ``no_active_matrix``.
        2. Find role-clusters the principal belongs to (membership =
           ``person_grace_id`` matches ``principal.user_id``).
        3. Walk each matched cluster's ``access_rules`` and match the
           rule's ``resource_label`` at three specificity tiers
           (F-031 / ISS-0013): exact label > class-level (rule label ==
           the resource *kind*, matching any instance of that kind) >
           wildcard ``"*"``. The most specific tier with any matching
           rule decides; within that tier an explicit deny is final
           (``explicit_deny``).
        4. If the winning tier allows the triple → allow, modulo
           agent-scope intersection for ``UserActingViaAgent``.
        5. Else apply ``matrix.default_decision``.

        For ``UserActingViaAgent``, an explicit allow is gated by the
        agent-scope intersection (D338): if the (kind, label, action)
        triple is not in the effective scope, deny with
        ``scope_intersection_empty``.
        """
        if self._matrix is None:
            return Deny(reason=EnforcementReason(code="no_active_matrix"))

        matching_clusters = list(self._find_clusters_for(principal))

        # F-031 / ISS-0013: strict-equality label matching made default-deny
        # unexpressible for per-entity resources — the retrieval post-filter
        # enforces on per-entity grace_ids, so under default-deny NO
        # ratifiable rule could ever allow a retrieval result (verified
        # live: every principal AND anonymous got 0/67 results). A rule's
        # resource_label now matches at one of three specificity tiers:
        #   0 — exact:       rule.resource_label == resource_label
        #   1 — class-level: rule.resource_label == resource_kind
        #                    (grants/denies every instance of that kind)
        #   2 — wildcard:    rule.resource_label == "*"
        # The most specific tier with any matching rule decides; within
        # that tier, explicit deny remains final (deny-bias preserved).
        kind_str = str(resource_kind)
        tier_decisions: dict[int, set[str]] = {0: set(), 1: set(), 2: set()}
        for cluster in matching_clusters:
            for rule in cluster.access_rules:
                if rule.resource_kind != resource_kind:
                    continue
                if rule.action != action:
                    continue
                if rule.resource_label == resource_label:
                    tier_decisions[0].add(rule.decision)
                elif rule.resource_label == kind_str:
                    tier_decisions[1].add(rule.decision)
                elif rule.resource_label == WILDCARD_RESOURCE_LABEL:
                    tier_decisions[2].add(rule.decision)

        explicit_deny = False
        explicit_allow = False
        for tier in (0, 1, 2):
            if tier_decisions[tier]:
                explicit_deny = "deny" in tier_decisions[tier]
                explicit_allow = not explicit_deny and (
                    "allow" in tier_decisions[tier]
                )
                break

        if explicit_deny:
            return Deny(reason=EnforcementReason(code="explicit_deny"))

        if explicit_allow:
            if isinstance(principal, UserActingViaAgent):
                eff = effective_scope(principal)
                # If agent intersection is empty, deny on intersection.
                if eff.is_empty():
                    return Deny(
                        reason=EnforcementReason(code="scope_intersection_empty")
                    )
                # If a non-empty effective scope exists but the requested
                # triple is not in it, deny on intersection.
                if not eff.admits(
                    str(resource_kind), str(resource_label), str(action)
                ):
                    return Deny(
                        reason=EnforcementReason(code="scope_intersection_empty")
                    )
            return Allow()

        # No matching rule on any matched cluster.
        if self._matrix.default_decision == "allow":
            return Allow()
        return Deny(reason=EnforcementReason(code="default_deny"))

    def _find_clusters_for(
        self, principal: PrincipalContext | User | UserActingViaAgent
    ) -> list[RoleCluster]:
        if self._matrix is None:
            return []
        user_id = getattr(principal, "user_id", None)
        if user_id is None:
            return []
        user_id_str = str(user_id)
        out: list[RoleCluster] = []
        for cluster in self._matrix.role_clusters:
            for member in cluster.members:
                if str(member.person_grace_id) == user_id_str:
                    out.append(cluster)
                    break
        return out


# ----- Module-level registry (rebound by ratify route) --------------


_REGISTRY: Enforcer = Enforcer(matrix=None)


def get_enforcer() -> Enforcer:
    """Return the process-wide ``Enforcer`` instance."""
    return _REGISTRY


def rebuild_enforcer(matrix: PermissionMatrix | None) -> None:
    """Atomically replace the active matrix on the module-level
    enforcer.

    The ratify route calls this after a successful
    ``repository.insert_matrix()``. ``permission_matrix_ratified``
    telemetry is emitted by the route, not by the enforcer.
    """
    _REGISTRY.rebuild_with(matrix)


def hydrate_enforcer_from_db(session_factory=None) -> bool:
    """Load the active matrix from ``permission_matrices`` into the enforcer.

    F-51 (validation run, 2026-07-01) capture-the-why: the module docstring
    claimed the active matrix is "loaded once on boot", but no boot path ever
    called it — the enforcer only rehydrated inside the in-process ratify route
    (D528 known gap). Every OTHER process (a restarted uvicorn, the MCP server)
    therefore ran with ``matrix is None`` → ``no_active_matrix``: post-restart
    retrieval enforcement silently reverted to no-matrix behavior and writable
    MCP tools were permanently denied despite a ratified matrix in the DB.

    Called from the FastAPI lifespan and the MCP server startup. Best-effort:
    a DB error leaves the enforcer dormant (matrix None) and logs a warning —
    boot must never crash on a missing/unreachable permissions store.

    Returns True if a matrix was loaded, False otherwise.
    """
    try:
        from src.permissions import repository as _repo
        from src.shared.database import get_session_factory

        factory = session_factory or get_session_factory()
        with factory() as session:
            row = _repo.get_active_matrix(session)
        if row is None:
            logger.info("enforcer.hydrate.no_active_matrix")
            return False
        matrix = PermissionMatrix.model_validate(row["payload"])
        rebuild_enforcer(matrix)
        logger.info(
            "enforcer.hydrate.loaded",
            clusters=len(matrix.role_clusters),
            default_decision=matrix.default_decision,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — boot must not crash on permissions store
        logger.warning("enforcer.hydrate.failed", error=str(exc))
        return False


__all__ = [
    "Enforcer",
    "Resource",
    "WILDCARD_RESOURCE_LABEL",
    "get_enforcer",
    "hydrate_enforcer_from_db",
    "rebuild_enforcer",
]
