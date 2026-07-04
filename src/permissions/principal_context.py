"""PrincipalContext envelope (Chunk 42, D338).

Discriminated union ``PrincipalContext = User | UserActingViaAgent`` that
threads through every authorization check. The agent variant is dormant
at v1 — Chunk 44 activates it via the agent-scoped runtime path. v1 ships
the intersection logic and the strict-visibility short-circuit so
Chunk 44 has nothing to design from scratch.

Intersection semantics (D338):

* ``intersect_scopes(user_scope, agent_scope)`` — AND-of-allows with
  deny-bias. The result is the set of (resource_kind, resource_label,
  action) triples that BOTH the user and the agent are entitled to.
  An agent NEVER escalates a user's permissions; a user NEVER escalates
  an agent's permissions; either party's deny is final.
* ``is_strict_visibility(mode)`` — returns True for visibility modes
  that bypass agent intersection entirely (currently only
  ``"private_to_self"`` per D295 / D339). Strict modes short-circuit at
  the resolver level; the agent scope is never consulted.

The ``from_admission_tree(request)`` factory constructs a ``User``
variant from the result of ``auth_middleware.AuthMiddleware`` (D236).
The agent variant is constructed by Chunk 44's agent-call adapter; in
v1 there is no public construction site for ``UserActingViaAgent``
beyond tests.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from src.permissions.models import VisibilityMode


# ----- Scope primitives ---------------------------------------------


ResourceKind = Literal[
    "ontology_module",
    "segment",
    "change_directive",
    "graph_entity",
    "retrieval_query_event",
]
"""Mirrors ``AccessRule.resource_kind`` exactly; do not drift."""


Action = Literal["view", "edit", "ratify"]


class ScopeEntry(BaseModel):
    """One allow or deny entry in a scope."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_kind: ResourceKind
    resource_label: str = Field(min_length=1)
    action: Action
    decision: Literal["allow", "deny"] = "allow"


class EffectiveScope(BaseModel):
    """The result of ``intersect_scopes(user_scope, agent_scope)``.

    ``allows`` is the set of triples both parties allow; ``denies``
    carries either party's explicit deny (deny-bias). A consumer that
    sees a triple in ``denies`` MUST treat it as denied regardless of
    any other entry.
    """

    model_config = ConfigDict(extra="forbid")

    allows: list[ScopeEntry] = Field(default_factory=list)
    denies: list[ScopeEntry] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.allows and not self.denies

    def admits(self, resource_kind: str, resource_label: str, action: str) -> bool:
        """Convenience: True iff (kind, label, action) is in ``allows``
        and NOT in ``denies``. Deny-bias enforced."""
        triple = (resource_kind, resource_label, action)
        for d in self.denies:
            if (d.resource_kind, d.resource_label, d.action) == triple:
                return False
        for a in self.allows:
            if (a.resource_kind, a.resource_label, a.action) == triple:
                return True
        return False


# ----- PrincipalContext discriminated union -------------------------


class User(BaseModel):
    """Plain user variant (v1 default).

    Constructed by ``from_admission_tree`` after AuthMiddleware admits
    the request.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["user"] = "user"
    user_id: UUID | None = Field(
        default=None,
        description=(
            "Canonical user identifier. ``None`` when the request was "
            "admitted via localhost-bypass without an X-Admin-Key "
            "header (D237)."
        ),
    )
    display_name: str | None = None
    admin_key_present: bool = False
    scope: list[ScopeEntry] = Field(default_factory=list)


class UserActingViaAgent(BaseModel):
    """Agent variant (dormant; Chunk 44 activates).

    The ``agent_scope_source`` distinguishes static-config (the agent
    has a profile burned in at config time) from per-call (the calling
    application narrates the agent's scope each request). v1 only ships
    ``"static_config"``; per-call is a Chunk 44 affordance.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["user_via_agent"] = "user_via_agent"
    user_id: UUID | None = None
    display_name: str | None = None
    admin_key_present: bool = False
    user_scope: list[ScopeEntry] = Field(default_factory=list)
    agent_id: str = Field(min_length=1)
    agent_display_name: str | None = None
    agent_scope: list[ScopeEntry] = Field(default_factory=list)
    agent_scope_source: Literal["static_config", "per_call"] = "static_config"


PrincipalContext = Annotated[
    User | UserActingViaAgent,
    Field(discriminator="kind"),
]


# ----- Factory ------------------------------------------------------


def from_admission_tree(request: Any) -> User:
    """Construct a ``User`` from a Starlette ``Request`` after the
    admission tree has admitted it.

    Dormancy guarantee: this factory NEVER returns ``UserActingViaAgent``.
    Chunk 44's agent-call adapter is the only legitimate construction
    site for the agent variant.

    The function reads optional state from ``request.state`` (set by
    AuthMiddleware on admit) without coupling to the middleware's
    private API: the only consumed attributes are ``user_id`` (UUID),
    ``display_name`` (str), and ``admin_key_present`` (bool). Missing
    attributes default safely.
    """
    state = getattr(request, "state", None)
    user_id_raw = getattr(state, "user_id", None) if state is not None else None
    display_name = (
        getattr(state, "user_display_name", None) if state is not None else None
    )
    admin_key_present = bool(
        getattr(state, "admin_key_present", False) if state is not None else False
    )

    user_id: UUID | None
    if isinstance(user_id_raw, UUID):
        user_id = user_id_raw
    elif isinstance(user_id_raw, str):
        try:
            user_id = UUID(user_id_raw)
        except (ValueError, TypeError):
            user_id = None
    else:
        user_id = None

    return User(
        user_id=user_id,
        display_name=display_name,
        admin_key_present=admin_key_present,
    )


# ----- Strict visibility short-circuit (D295/D339) -----------------


_STRICT_VISIBILITY_MODES: frozenset[str] = frozenset({"private_to_self"})


def is_strict_visibility(mode: VisibilityMode | str) -> bool:
    """Return True iff ``mode`` is a visibility mode that short-circuits
    BEFORE consulting the agent scope.

    Currently only ``"private_to_self"`` is strict — admin-key cannot
    bypass it (D295) and an agent intersection is never consulted (R4).
    Adding additional strict modes is an explicit future D-number; the
    set is intentionally narrow.
    """
    return str(mode) in _STRICT_VISIBILITY_MODES


# ----- Intersection -------------------------------------------------


def _entry_key(entry: ScopeEntry) -> tuple[str, str, str]:
    return (entry.resource_kind, entry.resource_label, entry.action)


def intersect_scopes(
    user_scope: list[ScopeEntry],
    agent_scope: list[ScopeEntry],
) -> EffectiveScope:
    """Intersect a user's scope with an agent's scope (D338).

    Semantics:

    * ``allows`` of the result = (allows of user) ∩ (allows of agent).
    * ``denies`` of the result = (denies of user) ∪ (denies of agent).
    * Deny is final: if either party explicitly denies a triple, that
      triple is removed from ``allows`` and added to ``denies``.

    This function is pure: no DB / network. It is also stable under
    repeated invocation — calling ``intersect_scopes(intersection,
    agent_scope).allows`` returns the same set as ``intersection.allows``
    when the input is already intersected (idempotency on already-narrow
    inputs).
    """
    user_allows = {_entry_key(e): e for e in user_scope if e.decision == "allow"}
    agent_allows = {_entry_key(e): e for e in agent_scope if e.decision == "allow"}
    user_denies = {_entry_key(e): e for e in user_scope if e.decision == "deny"}
    agent_denies = {_entry_key(e): e for e in agent_scope if e.decision == "deny"}

    union_denies: dict[tuple[str, str, str], ScopeEntry] = {}
    union_denies.update(user_denies)
    union_denies.update(agent_denies)

    common_keys = set(user_allows.keys()) & set(agent_allows.keys())
    intersection_allows: dict[tuple[str, str, str], ScopeEntry] = {}
    for key in common_keys:
        if key in union_denies:
            continue
        # Prefer the user-side entry (label-equal so identity is moot).
        intersection_allows[key] = user_allows[key]

    return EffectiveScope(
        allows=sorted(
            intersection_allows.values(),
            key=_entry_key,
        ),
        denies=sorted(
            union_denies.values(),
            key=_entry_key,
        ),
    )


def effective_scope(principal: User | UserActingViaAgent) -> EffectiveScope:
    """Return the ``EffectiveScope`` for any ``PrincipalContext`` variant.

    For ``User`` this is just the user's allows promoted into an
    ``EffectiveScope``; for ``UserActingViaAgent`` it is the
    intersection of the user and agent scopes.
    """
    if principal.kind == "user":
        return EffectiveScope(
            allows=[e for e in principal.scope if e.decision == "allow"],
            denies=[e for e in principal.scope if e.decision == "deny"],
        )
    return intersect_scopes(principal.user_scope, principal.agent_scope)


__all__ = [
    "Action",
    "EffectiveScope",
    "PrincipalContext",
    "ResourceKind",
    "ScopeEntry",
    "User",
    "UserActingViaAgent",
    "effective_scope",
    "from_admission_tree",
    "intersect_scopes",
    "is_strict_visibility",
]
