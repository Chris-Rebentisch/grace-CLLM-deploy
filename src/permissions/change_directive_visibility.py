"""Per-mode visibility resolution for Change Directives (D296 → D339).

Pure-ish function — Enforcer lookup only when ``scoped_to_role_cluster``
is in play. The on-row ``visibility`` enum literal strings are
byte-identical to the Chunk 38 stub (D285 forward-guarantee held).

Resolution rules (Chunk 42):

- ``permission_matrix_default`` — admits author + admin override.
- ``private_to_self`` — strict; admits **author only**. Admin-key does
  NOT override (R4); the agent intersection is never consulted (D295).
  This branch is the candid-draft mode.
- ``private_to_named_list`` — admits author OR users whose ``user_id``
  appears in ``visibility_named_list``.
- ``scoped_to_role_cluster`` — D339 closure: consults
  ``Enforcer.enforce(principal, "change_directive",
  resource_label="<directive_id>", "view")``. Author and admin are
  short-circuited to admit (the directive's owner can always read it,
  and admin override mirrors ``permission_matrix_default``). Non-author,
  non-admin requests fall through to the active matrix; with no active
  matrix, the enforcer defaults to deny (OWASP A01).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.permissions.enforcer import get_enforcer
from src.permissions.models import Allow
from src.permissions.principal_context import User


def _as_uuid(value: Any) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _directive_id(directive: dict[str, Any] | Any) -> str | None:
    if isinstance(directive, dict):
        for key in ("change_directive_id", "directive_id", "id"):
            v = directive.get(key)
            if v is not None:
                return str(v)
        return None
    for attr in ("change_directive_id", "directive_id", "id"):
        v = getattr(directive, attr, None)
        if v is not None:
            return str(v)
    return None


def resolve_visibility(
    directive: dict[str, Any] | Any,
    requesting_user: UUID | str,
    *,
    admin_key_present: bool,
) -> bool:
    """Return True iff ``requesting_user`` may access ``directive``.

    Args:
        directive: row dict (from repository) OR Pydantic instance with
            ``authored_by``, ``visibility``, ``visibility_named_list``
            attributes / keys. May also expose
            ``change_directive_id`` / ``directive_id`` / ``id`` for the
            ``scoped_to_role_cluster`` enforcer label.
        requesting_user: caller identity. ``admin_key_present`` does not
            override ``private_to_self`` (R4).
        admin_key_present: True when the request was authenticated with
            ``X-Admin-Key`` (D236-style admin override).
    """
    if isinstance(directive, dict):
        authored_by = _as_uuid(directive.get("authored_by"))
        visibility = directive.get("visibility") or "permission_matrix_default"
        named_list = directive.get("visibility_named_list") or []
    else:
        authored_by = _as_uuid(getattr(directive, "authored_by", None))
        visibility = getattr(
            directive, "visibility", "permission_matrix_default"
        )
        named_list = getattr(directive, "visibility_named_list", None) or []

    requesting = _as_uuid(requesting_user)

    is_author = (
        authored_by is not None
        and requesting is not None
        and authored_by == requesting
    )

    if visibility == "private_to_self":
        # Strict — admin does NOT override (R4); agent intersection never
        # consulted (D295). The candid-draft mode.
        return is_author

    if visibility == "private_to_named_list":
        if is_author:
            return True
        if requesting is None:
            return False
        return str(requesting) in {str(x) for x in named_list}

    if visibility == "permission_matrix_default":
        return is_author or admin_key_present

    if visibility == "scoped_to_role_cluster":
        # D339 closure: consult the Enforcer for non-author, non-admin
        # callers. Author + admin short-circuit to admit (mirrors the
        # default mode for directive owners).
        if is_author or admin_key_present:
            return True
        if requesting is None:
            return False
        enforcer = get_enforcer()
        principal = User(user_id=requesting, admin_key_present=False)
        directive_label = _directive_id(directive) or "*"
        decision = enforcer.enforce(
            principal,
            "change_directive",
            directive_label,
            "view",
        )
        return isinstance(decision, Allow)

    # Unknown mode — deny.
    return False
