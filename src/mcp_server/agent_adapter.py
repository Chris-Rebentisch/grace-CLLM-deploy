"""Agent-call adapter (Chunk 44, D364).

Sole production construction site for ``UserActingViaAgent``. Reads
agent identity from environment variables and intersects agent scope
with the user scope via ``intersect_scopes`` (AND-of-allows,
deny-bias).

When the three agent env vars are absent, the adapter degrades
gracefully to returning the plain ``User`` — no agent overlay.

R12 invariant: this module NEVER imports or constructs
``SystemPrincipal``. AST-gated by
``tests/mcp_server/test_no_system_principal_import.py``.
"""

from __future__ import annotations

import os

import structlog

from src.permissions.principal_context import (
    ScopeEntry,
    User,
    UserActingViaAgent,
    intersect_scopes,
)

log = structlog.get_logger()


def _parse_scope(scope_str: str) -> list[ScopeEntry]:
    """Parse a comma-separated scope string into ScopeEntry objects.

    Format: ``resource_kind:resource_label:action[:decision]``
    where decision defaults to ``"allow"``.

    Example: ``ontology_module:finance:view,segment:legal:edit``
    """
    entries: list[ScopeEntry] = []
    if not scope_str.strip():
        return entries
    for part in scope_str.split(","):
        part = part.strip()
        if not part:
            continue
        segments = part.split(":")
        if len(segments) < 3:
            log.warning(
                "agent_adapter.invalid_scope_entry",
                entry=part,
                reason="expected at least resource_kind:resource_label:action",
            )
            continue
        decision = segments[3] if len(segments) > 3 else "allow"
        entries.append(
            ScopeEntry(
                resource_kind=segments[0],  # type: ignore[arg-type]
                resource_label=segments[1],
                action=segments[2],  # type: ignore[arg-type]
                decision=decision,  # type: ignore[arg-type]
            )
        )
    return entries


def resolve_principal_with_agent(
    user: User,
) -> User | UserActingViaAgent:
    """Resolve the principal, optionally overlaying agent identity.

    When ``GRACE_AGENT_ID``, ``GRACE_AGENT_DISPLAY_NAME``, and
    ``GRACE_AGENT_SCOPE`` are all present in the environment, constructs
    a ``UserActingViaAgent`` with ``intersect_scopes`` applied. When any
    env var is absent, returns the plain ``User`` unchanged.

    The ``agent_scope_source`` is always ``"static_config"`` at v1.
    """
    agent_id = os.environ.get("GRACE_AGENT_ID", "")
    agent_display_name = os.environ.get("GRACE_AGENT_DISPLAY_NAME", "")
    agent_scope_str = os.environ.get("GRACE_AGENT_SCOPE", "")

    if not agent_id or not agent_display_name:
        return user

    agent_scope = _parse_scope(agent_scope_str)

    # Intersect to verify the agent has any permissions at all.
    effective = intersect_scopes(user.scope, agent_scope)
    if effective.is_empty() and agent_scope:
        log.warning(
            "agent_adapter.scope_intersection_empty",
            agent_id=agent_id,
            reason="agent scope does not overlap with user scope",
        )

    return UserActingViaAgent(
        user_id=user.user_id,
        display_name=user.display_name,
        admin_key_present=user.admin_key_present,
        user_scope=user.scope,
        agent_id=agent_id,
        agent_display_name=agent_display_name,
        agent_scope=agent_scope,
        agent_scope_source="static_config",
    )
