"""Write-time ontology type enforcement (F-09).

validation-run F-09: the graph write path silently AUTO-CREATED undefined
vertex types — `CREATE (n:Contractor ...)` materialized a `Contractor` type in
ArcadeDB even though no such type was ever human-approved, contradicting the
documented invariant ("undefined types fail at the DB") and the core product
principle that the ratified ontology defines what a deployment is permitted to
know.

This module supplies the single validation gate used by the graph API's entity
insert routes. The allowlist is the union of:

1. every ``entity_types`` key across ALL modules of the ACTIVE ontology
   version (the human-approved boundary);
2. the system/meta plane, which is deliberately outside the business ontology:
   ``META_ENTITY_TYPES`` (provenance/audit vertices), the intent layer
   (D531), and namespace label-prefixed types (Chunk 51 federation DDL);
3. nothing else.

Enforcement mode via ``GRACE_TYPE_ENFORCEMENT`` env var:
- ``enforce`` (default) — undefined types are rejected (422 at the route);
- ``warn``    — log-and-allow (migration/bulk-backfill escape hatch);
- ``off``     — disable entirely (not recommended; documented for parity with
  other guards).

The allowlist is TTL-cached (60s) so per-insert overhead is one dict lookup;
ratifying a new ontology version is visible within the TTL window.
"""

from __future__ import annotations

import os
import time

import structlog

logger = structlog.get_logger()

_TTL_SECONDS = 60.0
_cache: dict = {"allowed": None, "checked_at": 0.0, "ontology_seen": False}

# System/meta vertex types that are legitimately outside the business
# ontology. Graph labels, not Pydantic class names.
_SYSTEM_VERTEX_TYPES: frozenset[str] = frozenset(
    {
        # Intent layer (D531) — meta-plane, module tag "intent".
        "Decision_Principle",
        "Decision_Rationale",
        "Counterfactual",
        "Mandatory_Provision",
    }
)


def enforcement_mode() -> str:
    """Current mode: enforce (default) | warn | off."""
    mode = os.environ.get("GRACE_TYPE_ENFORCEMENT", "enforce").strip().lower()
    return mode if mode in {"enforce", "warn", "off"} else "enforce"


def invalidate_type_cache() -> None:
    """Reset the allowlist cache (test hook / post-ratify hint)."""
    _cache["allowed"] = None
    _cache["checked_at"] = 0.0
    _cache["ontology_seen"] = False


def _build_allowlist() -> tuple[set[str], bool]:
    """Return (allowed types, ontology_seen).

    ``ontology_seen`` is False when no active ontology version could be read —
    enforcement does not engage in that state (the human-approved boundary
    begins at first ratification; a fresh/half-initialized deployment must not
    have its writes bricked by an empty boundary).
    """
    ontology_seen = False
    allowed: set[str] = set(_SYSTEM_VERTEX_TYPES)

    # META provenance/audit types (Extraction_Event, Query_Event, ...).
    try:
        from src.graph.migration_types import META_ENTITY_TYPES

        allowed.update(META_ENTITY_TYPES.keys())
    except Exception as exc:  # noqa: BLE001
        logger.warning("type_enforcement.meta_types_unavailable", error=str(exc))

    # Active ontology: union of entity_types across ALL modules.
    try:
        from src.ontology.database import get_active_version
        from src.shared.database import get_session_factory

        session = get_session_factory()()
        try:
            active = get_active_version(session)
            if active is not None:
                ontology_seen = True
                schema_json = active.schema_json or {}
                allowed.update((schema_json.get("entity_types") or {}).keys())
                for module_content in (active.schema_modules or {}).values():
                    allowed.update(
                        ((module_content or {}).get("entity_types") or {}).keys()
                    )
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("type_enforcement.ontology_read_failed", error=str(exc))

    # Federation label-prefixed types (Chunk 51): <Prefix>_Entity plus
    # <Prefix>_<OntologyType> for registered namespaces.
    try:
        from src.graph.namespace_database import GraphNamespaceRow
        from src.shared.database import get_session_factory

        session = get_session_factory()()
        try:
            prefixes = [
                row.label_prefix
                for row in session.query(GraphNamespaceRow)
                .filter(GraphNamespaceRow.label_prefix.isnot(None))
                .all()
            ]
        finally:
            session.close()
        for prefix in prefixes:
            allowed.add(f"{prefix}_Entity")
            for t in list(allowed):
                if not t.startswith(f"{prefix}_"):
                    allowed.add(f"{prefix}_{t}")
    except Exception as exc:  # noqa: BLE001
        logger.warning("type_enforcement.namespace_read_failed", error=str(exc))

    return allowed, ontology_seen


def get_allowed_vertex_types() -> set[str]:
    """TTL-cached allowlist of writable vertex types."""
    _refresh_cache()
    return _cache["allowed"]


def _refresh_cache() -> None:
    now = time.monotonic()
    if (
        _cache["allowed"] is not None
        and (now - _cache["checked_at"]) < _TTL_SECONDS
    ):
        return
    allowed, ontology_seen = _build_allowlist()
    _cache["allowed"] = allowed
    _cache["ontology_seen"] = ontology_seen
    _cache["checked_at"] = now


class UndefinedEntityTypeError(ValueError):
    """Raised when a write names a vertex type outside the approved boundary."""


def validate_entity_type(entity_type: str) -> None:
    """Gate a vertex type against the approved boundary (F-09).

    Raises UndefinedEntityTypeError in ``enforce`` mode; logs in ``warn``
    mode; no-ops in ``off`` mode. An EMPTY allowlist (no active ontology and
    meta registry unreadable) fails open with a warning — a half-initialized
    deployment must not brick provenance writes.
    """
    mode = enforcement_mode()
    if mode == "off":
        return

    _refresh_cache()
    allowed = _cache["allowed"]
    if not _cache["ontology_seen"]:
        # No active ontology ratified/readable — the boundary begins at first
        # ratification; do not brick fresh-deployment or provenance writes.
        return
    if not allowed or entity_type in allowed:
        return

    if mode == "warn":
        logger.warning(
            "type_enforcement.undefined_type_allowed",
            entity_type=entity_type,
            mode="warn",
        )
        return

    raise UndefinedEntityTypeError(
        f"Entity type '{entity_type}' is not defined in the active ontology "
        "or the system/meta plane. The ratified ontology defines what this "
        "deployment may know (F-09); ratify the type first, or set "
        "GRACE_TYPE_ENFORCEMENT=warn for a migration window."
    )
