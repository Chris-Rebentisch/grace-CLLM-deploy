"""Reconciliation Bridge integration helper (D297).

``find_covering_directives`` returns active Change Directives that
"cover" a given segment (and optionally a specific element_name within
that segment). Coverage means: status='active' AND segment is in
``affected_segments`` AND visibility resolves true for the requesting
user.

Wired into :mod:`src.api.recon_routes` so Gap Reports and Divergence
Maps surface "change-in-flight" framing to the reviewer.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.change_directives import repository as cd_repository
from src.change_directives.models import CoveringDirective, DirectiveStatus
from src.permissions.change_directive_visibility import resolve_visibility


def find_covering_directives(
    db: Session,
    segment_id: str,
    element_name: str | None,
    requesting_user: UUID,
    *,
    admin_key_present: bool = False,
) -> list[CoveringDirective]:
    """Return active directives whose ``affected_segments`` include
    ``segment_id`` and which are visible to ``requesting_user``.

    Empty list when no covering directive matches; the caller always
    surfaces ``covering_directives`` (never null per D297 contract).

    ``element_name`` is reserved for future scoping (currently unused —
    v1 returns all active directives covering the segment).
    """
    rows = db.execute(
        text(
            """
            SELECT directive_id, tier, title, status, authored_by,
                   authored_at, visibility, visibility_named_list,
                   visibility_role_cluster, affected_segments
              FROM change_directives
             WHERE status = :active
               AND affected_segments @> CAST(:segment AS jsonb)
            """
        ),
        {
            "active": DirectiveStatus.ACTIVE.value,
            "segment": json.dumps([segment_id]),
        },
    ).mappings().all()

    out: list[CoveringDirective] = []
    for row in rows:
        directive = dict(row)
        if not resolve_visibility(
            directive, requesting_user, admin_key_present=admin_key_present
        ):
            continue
        affected = directive.get("affected_segments") or []
        if isinstance(affected, str):
            try:
                affected = json.loads(affected)
            except ValueError:
                affected = []
        out.append(
            CoveringDirective(
                directive_id=directive["directive_id"],
                tier=str(directive["tier"]),
                title=str(directive["title"]),
                status=DirectiveStatus(directive["status"]),
                authored_at=directive["authored_at"],
                affected_segments=list(affected),
            )
        )
    return out


def enrich_covering_directives_realization(
    db: Session, items: list[CoveringDirective]
) -> list[CoveringDirective]:
    """Attach latest snapshot realization fields when present (D305)."""
    out: list[CoveringDirective] = []
    for cd in items:
        drow = cd_repository.get_by_id(db, cd.directive_id)
        snap = cd_repository.get_latest_snapshot(db, cd.directive_id)
        if drow and snap:
            stalled = cd_repository.compute_is_stalled_for_directive(
                db, cd.directive_id, drow
            )
            band = cd_repository.compute_velocity_band(snap, stalled)
            prog = snap.get("progress_percentage")
            prog_f = float(prog) if prog is not None else None
            out.append(
                cd.model_copy(
                    update={
                        "progress_percentage": prog_f,
                        "velocity_band": band,
                        "is_stalled": stalled,
                    }
                )
            )
        else:
            out.append(cd)
    return out
