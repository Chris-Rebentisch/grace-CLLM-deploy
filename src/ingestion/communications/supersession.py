"""Final-message supersession for email thread reconstruction (Chunk 80a, D514).

Detects contradictions within reconstructed threads and closes earlier facts
via ``valid_to`` + ``superseded_by``.

D356 capture-the-why: D514 — final-message supersession; ``valid_to`` close
+ ``superseded_by`` write. This module MUST NOT import ``fastapi`` or ``apscheduler``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import structlog

from src.graph.entity_ops import _extract_node
from src.graph.system_properties import VERTEX_SYSTEM_PROPERTIES

logger = structlog.get_logger()

_VERTEX_SYSTEM_NAMES = frozenset(p["name"] for p in VERTEX_SYSTEM_PROPERTIES) | {
    "_embedding",
    "@rid",
    "@type",
    # F-29 (validation run): identity/system properties that are NOT
    # supersedable facts. `name` is a vertex identity label (never a
    # thread-contradictable assertion), and these are provenance/id fields on
    # system vertices; treating them as supersedable produced spurious
    # supersession writes across Extraction_Event / Query_Event vertices.
    "name",
    "@out",
    "@in",
    "content",
    "content_sha256",
    "chunk_index",
    "source_path",
    "media_type",
}

# F-29 (validation run): system/provenance vertex types are graph
# plumbing, not thread-scoped domain entities. Thread supersession previously
# excluded only Document_Chunk, so it swept Extraction_Event provenance
# vertices (and would sweep Query/Response/Image system vertices) into the
# thread-entity set and tried to supersede their `name`/system properties.
_SYSTEM_VERTEX_TYPES = frozenset({
    "Extraction_Event",
    "Query_Event",
    "Response_Event",
    "Image_Asset",
    "Document_Chunk",
})


def _entity_identity_key(entity: dict) -> tuple[str, str]:
    """Derive the identity-grouping key for a thread entity.

    F-0032(b) / ISS-0036 capture-the-why: the original grouping keyed
    contradictions by ``(entity_type, property_name)`` across the WHOLE
    thread, so two DIFFERENT Persons with different ``full_name`` values were
    counted as a "contradiction" and the earlier PERSON was wrongly marked
    superseded (4 Person vertices + 1 Lease in the validation run).
    Supersession may only compare claims about the SAME entity:

    - primary identity evidence: the vertex identity label (``name``, carried
      separately as ``entity_name`` because F-29 strips it from supersedable
      properties) or an identity-bearing domain property (``full_name``).
    - fallback when NO name evidence exists: each vertex is its own identity
      (keyed by ``grace_id``) — conservative, because cross-vertex
      supersession requires positive same-entity evidence. Same-``grace_id``
      duplicates still group together via the grace-key.
    """
    entity_type = entity.get("entity_type", "")
    props = entity.get("properties", {}) or {}
    name = (
        entity.get("entity_name")
        or props.get("full_name")
        or props.get("name")
        or ""
    )
    name_norm = str(name).strip().lower()
    if name_norm:
        return (entity_type, f"name:{name_norm}")
    return (entity_type, f"grace:{entity.get('grace_id', '')}")


def _classify_value_change(earlier_value: Any, later_value: Any) -> str:
    """Classify the relationship between two property values.

    Returns one of:
        - "same": values are equivalent
        - "refinement": later adds detail to earlier (not a contradiction)
        - "contradiction": values are materially different
        - "ambiguous": cannot definitively determine

    A contradiction is when the same property has a materially different value.
    A refinement (added detail without contradiction) is NOT a contradiction.
    """
    if earlier_value is None or later_value is None:
        return "same"  # Missing value is not actionable

    # Normalize for comparison
    ev = str(earlier_value).strip().lower()
    lv = str(later_value).strip().lower()

    if ev == lv:
        return "same"

    # If earlier value is contained in later value → refinement (added detail)
    if ev in lv:
        return "refinement"

    # If later is contained in earlier → ambiguous (possible reduction)
    if lv in ev:
        return "ambiguous"

    # Neither contains the other → clear contradiction
    return "contradiction"


def apply_thread_supersession(
    thread_id: str,
    thread_entities: list[dict],
    arcade_client: Any | None = None,
    session: Any | None = None,
) -> dict:
    """Apply supersession logic within a reconstructed thread.

    For each (entity_type, property_name) pair, if the thread-final message
    asserts a different value than an earlier message, close the earlier
    entity's ``valid_to`` and set ``superseded_by``.

    Auto-supersede fires ONLY on explicit value conflict (the extraction
    verdict for the earlier claim is effectively REFUTED by the later claim).
    Refinements (added detail without contradiction) do NOT supersede.
    Ambiguous cases emit D101 constraint-validator temporal-overlap WARNING.

    Args:
        thread_id: The thread identifier (root message-id).
        thread_entities: List of dicts with keys:
            - ``grace_id``: entity grace_id
            - ``entity_type``: vertex type
            - ``properties``: dict of property values
            - ``source_message_id``: which email produced this entity
            - ``thread_position``: position in thread (0=root)
            - ``sent_at``: timestamp of the source message
        arcade_client: ArcadeDB client for writing supersession (optional for unit tests).
        session: SQLAlchemy session (optional for unit tests).

    Returns:
        Summary dict with superseded_count, preserved_count, ambiguous_count.
    """
    updates, result = _compute_supersession_updates(thread_id, thread_entities)
    # SYNC write path (unit tests / standalone callers). The production reconstructor
    # uses the async one-loop path (apply_thread_supersession_async).
    if arcade_client and updates:
        for upd in updates:
            try:
                _apply_supersession_write(
                    arcade_client,
                    upd["superseded_grace_id"],
                    upd["superseding_grace_id"],
                    upd["valid_to"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "supersession.write_failed",
                    superseded_grace_id=upd["superseded_grace_id"],
                    error=str(exc),
                )
    return result


def _compute_supersession_updates(
    thread_id: str, thread_entities: list[dict]
) -> tuple[list[dict], dict]:
    """Pure computation (no I/O): detect intra-thread contradictions and build the
    list of supersession writes + summary counts. Shared by the sync
    ``apply_thread_supersession`` and async ``apply_thread_supersession_async``."""
    if not thread_entities:
        return [], {"superseded_count": 0, "preserved_count": 0, "ambiguous_count": 0}

    # F-0032(b) / ISS-0036: group assertions by (ENTITY IDENTITY, property_name),
    # not (entity_type, property_name). Only claims about the SAME entity
    # (same identity name; fallback same grace_id) can supersede each other —
    # two different Persons must never be a "contradiction".
    property_assertions: dict[tuple[tuple[str, str], str], list[dict]] = {}
    for entity in thread_entities:
        identity_key = _entity_identity_key(entity)
        properties = entity.get("properties", {})
        position = entity.get("thread_position", 0)
        for prop_name, prop_value in properties.items():
            key = (identity_key, prop_name)
            if key not in property_assertions:
                property_assertions[key] = []
            property_assertions[key].append({
                "grace_id": entity["grace_id"],
                "value": prop_value,
                "position": position,
                "sent_at": entity.get("sent_at"),
                "entity": entity,
            })

    superseded_count = 0
    preserved_count = 0
    ambiguous_count = 0
    supersession_updates: list[dict] = []
    for (identity_key, prop_name), assertions in property_assertions.items():
        entity_type = identity_key[0]
        if len(assertions) < 2:
            preserved_count += 1
            continue
        assertions.sort(key=lambda a: a["position"])
        final = assertions[-1]
        for earlier in assertions[:-1]:
            # F-0032(c) / ISS-0036: when earlier and later claims resolved into
            # ONE merged vertex there is no earlier/later vertex pair — a
            # vertex-level superseded_by write would be a self-referencing
            # no-op. That case belongs to claim-level supersession
            # (apply_claim_level_supersession); skip it here.
            if earlier["grace_id"] == final["grace_id"]:
                preserved_count += 1
                continue
            classification = _classify_value_change(earlier["value"], final["value"])
            if classification == "contradiction":
                supersession_updates.append({
                    "superseded_grace_id": earlier["grace_id"],
                    "superseding_grace_id": final["grace_id"],
                    "valid_to": final.get("sent_at"),
                    "entity_type": entity_type,
                    "property_name": prop_name,
                })
                superseded_count += 1
                logger.info(
                    "supersession.contradiction_detected",
                    thread_id=thread_id, entity_type=entity_type, property_name=prop_name,
                    superseded_grace_id=earlier["grace_id"],
                    superseding_grace_id=final["grace_id"],
                )
            elif classification == "ambiguous":
                ambiguous_count += 1
                logger.warning(
                    "supersession.ambiguous_contradiction",
                    thread_id=thread_id, entity_type=entity_type, property_name=prop_name,
                    earlier_grace_id=earlier["grace_id"], later_grace_id=final["grace_id"],
                    rule="temporal_overlap", severity="WARNING",
                    detail=(
                        "Cannot definitively determine contradiction vs refinement. "
                        "D101 constraint-validator temporal-overlap WARNING."
                    ),
                )
            else:
                # refinement (added detail) or same value — preserve
                preserved_count += 1

    return supersession_updates, {
        "superseded_count": superseded_count,
        "preserved_count": preserved_count,
        "ambiguous_count": ambiguous_count,
    }


async def apply_thread_supersession_async(
    thread_id: str,
    thread_entities: list[dict],
    arcade_client: Any,
    session: Any | None = None,
) -> dict:
    """Async one-loop supersession apply (D541 round-2). Awaits each write in the
    CURRENT event loop, so the production reconstructor can drive all per-source
    loads + writes inside a single ``asyncio.run`` with one pooled client and one
    ``aclose`` — eliminating the per-call ``reset_pool`` orphaned-client leak."""
    updates, result = _compute_supersession_updates(thread_id, thread_entities)
    if arcade_client and updates:
        for upd in updates:
            try:
                await _apply_supersession_write_async(
                    arcade_client,
                    upd["superseded_grace_id"],
                    upd["superseding_grace_id"],
                    upd["valid_to"],
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "supersession.write_failed",
                    superseded_grace_id=upd["superseded_grace_id"],
                    error=str(exc),
                )
    return result


def _build_supersession_query(
    superseded_grace_id: str, superseding_grace_id: str, valid_to: datetime | None
) -> str:
    """Build the OpenCypher MATCH...SET that stamps superseded_by (+ valid_to)."""
    from src.graph.cypher_utils import escape_cypher_string

    escaped_old = escape_cypher_string(superseded_grace_id)
    escaped_new = escape_cypher_string(superseding_grace_id)
    query = (
        f"MATCH (n {{grace_id: '{escaped_old}'}}) "
        f"SET n.superseded_by = '{escaped_new}'"
    )
    if valid_to:
        query += f", n.valid_to = '{valid_to.isoformat()}'"
    return query + " RETURN n.grace_id"


async def _apply_supersession_write_async(
    arcade_client: Any,
    superseded_grace_id: str,
    superseding_grace_id: str,
    valid_to: datetime | None,
) -> None:
    """Await the supersession write in the CURRENT event loop.

    D541 (round-2 refactor): the one-loop production path (see
    thread_reconstructor) drives all per-source loads + writes inside a single
    ``asyncio.run`` with one pooled client + one ``aclose`` — so no per-call
    ``reset_pool``/loop-boundary juggling and no orphaned httpx clients."""
    await arcade_client.execute_cypher(
        _build_supersession_query(superseded_grace_id, superseding_grace_id, valid_to)
    )


def _apply_supersession_write(
    arcade_client: Any,
    superseded_grace_id: str,
    superseding_grace_id: str,
    valid_to: datetime | None,
) -> None:
    """Write supersession to ArcadeDB vertex via SQL UPDATE (SYNC fallback path).

    D514 — sets ``valid_to`` and ``superseded_by`` on the superseded entity.
    Retained for the sync ``apply_thread_supersession`` path + unit tests; the
    production reconstructor uses the async one-loop path (D541 round-2 refactor).
    """
    import asyncio

    query = _build_supersession_query(superseded_grace_id, superseding_grace_id, valid_to)

    # D541 capture-the-why: the old juggling reused the arcade client's pooled
    # httpx client across asyncio.run() boundaries — the client was bound to a
    # prior, now-closed loop, so the write raised "Event loop is closed" and (worse)
    # the loop.is_running() branch did fire-and-forget ensure_future, dropping the
    # write entirely. Reset the pool so the client is recreated in THIS loop, and
    # actually AWAIT the write.
    async def _do_write() -> None:
        if hasattr(arcade_client, "reset_pool"):
            arcade_client.reset_pool()
        await arcade_client.execute_cypher(query)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Normal sync CLI path: no running loop.
        asyncio.run(_do_write())
    else:
        # Rare: invoked from within a running loop. Run in a worker thread with its
        # own loop so the write completes (never fire-and-forget).
        import threading

        box: dict[str, Any] = {}

        def _worker() -> None:
            try:
                asyncio.run(_do_write())
            except Exception as exc:  # noqa: BLE001
                box["err"] = exc

        th = threading.Thread(target=_worker)
        th.start()
        th.join()
        if "err" in box:
            raise box["err"]


async def fetch_thread_entities_from_arcade(
    arcade_client: Any,
    members: list[dict],
) -> list[dict]:
    """Load graph entities for thread messages via ``source_document_id`` (email:…).

    D514 — ArcadeDB query step in CP4 runtime trace; called from thread reconstructor
    after position assignment (CP3 step 5).
    """
    from src.graph.cypher_utils import escape_cypher_string

    thread_entities: list[dict] = []

    for member in members:
        message_id = member["message_id"]
        doc_id = f"email:{message_id}"
        escaped_doc = escape_cypher_string(doc_id)
        query = (
            f"MATCH (n) "
            f"WHERE n.source_document_id = '{escaped_doc}' "
            f"RETURN n"
        )
        result = await arcade_client.execute_cypher(query)
        rows = result.get("result", [])

        for row in rows:
            node = _extract_node(row)
            if not node.get("grace_id"):
                continue
            entity_type = node.get("@type") or node.get("entity_type") or ""
            # F-29 (validation run): exclude ALL system vertex types, not
            # just Document_Chunk, from thread-entity fetching.
            if entity_type in _SYSTEM_VERTEX_TYPES:
                continue
            domain_props = {
                k: v
                for k, v in node.items()
                if k not in _VERTEX_SYSTEM_NAMES and v is not None
            }
            thread_entities.append({
                "grace_id": node["grace_id"],
                "entity_type": entity_type,
                # F-0032(b) / ISS-0036: carry the vertex identity label
                # separately. F-29 strips `name` from supersedable properties
                # (it is identity, not a fact), but identity keying in
                # _compute_supersession_updates NEEDS it to decide whether two
                # vertices are the same entity.
                "entity_name": node.get("name"),
                "properties": domain_props,
                "source_message_id": message_id,
                "thread_position": member.get("thread_position", 0),
                "sent_at": member.get("sent_at"),
            })

    return thread_entities


def load_thread_entities_from_arcade(
    arcade_client: Any,
    members: list[dict],
) -> list[dict]:
    """Sync wrapper for ``fetch_thread_entities_from_arcade`` (CLI path)."""
    import asyncio

    # D541: reset the pooled client so it is recreated in the fresh asyncio.run loop
    # (the prior per-thread asyncio.run loop is closed; reusing the bound client
    # raises "Event loop is closed").
    async def _fetch() -> list[dict]:
        if hasattr(arcade_client, "reset_pool"):
            arcade_client.reset_pool()
        return await fetch_thread_entities_from_arcade(arcade_client, members)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_fetch())
    else:
        # Nested loop — return empty; caller may pass pre-built entities in tests.
        logger.warning("supersession.fetch_skipped_running_loop")
        return []


def apply_claim_level_supersession(
    session: Any,
    thread_id: str,
    members: list[dict],
    dry_run: bool = False,
) -> dict:
    """Claim-level supersession for contradictions that ER-dedup merged into ONE vertex.

    F-0032(c) / ISS-0036 capture-the-why: when an earlier claim ($18,500) and a
    later corrected claim ($15,800) resolve into a SINGLE merged vertex, there
    is no earlier/later vertex pair, so vertex-level ``superseded_by`` is
    unobservable BY DESIGN. The claim table already carries the supersession
    vocabulary (``supersedes_claim_id`` on the superseding claim +
    ``status='superseded'`` on the superseded claim — same semantics as the
    Chunk 30 Edit-and-Accept flow in ``src/api/claim_routes.py``), so claims
    are the system of record for merged-vertex contradictions. Vertex-level
    ``superseded_by`` remains for genuinely distinct vertex pairs (see
    ``_compute_supersession_updates``).

    Scope boundary (what these two modules can honestly do): only ENTITY
    claims with a populated ``resolved_entity_grace_id`` and property values in
    ``properties_json`` are considered. Relationship claims, unresolved claims,
    and claims from the same message (no earlier/later ordering) are out of
    scope. ``supersedes_claim_id`` is single-valued, so when several earlier
    claims are superseded by one final claim, the pointer records the most
    recent earlier claim; the others still flip to ``status='superseded'``.

    Args:
        session: SQLAlchemy session (mocked in unit tests).
        thread_id: Thread identifier (root message-id) — logging context.
        members: Thread member dicts with ``message_id`` + ``thread_position``.

    Returns:
        Summary dict: ``claims_superseded``, ``pointer_writes``,
        ``ambiguous_count``, ``groups_checked``.
    """
    from sqlalchemy import bindparam, text

    empty = {
        "claims_superseded": 0,
        "pointer_writes": 0,
        "ambiguous_count": 0,
        "groups_checked": 0,
    }
    if not members:
        return empty

    # source_document_id for email-derived claims is "email:<message_id>"
    # (extraction_bridge contract).
    position_by_doc: dict[str, int] = {
        f"email:{m['message_id']}": m.get("thread_position", 0) for m in members
    }
    doc_ids = list(position_by_doc)

    stmt = text(
        "SELECT claim_id, source_document_id, resolved_entity_grace_id, "
        "properties_json, status, supersedes_claim_id, created_at "
        "FROM extraction_claims "
        "WHERE source_document_id IN :doc_ids "
        "AND resolved_entity_grace_id IS NOT NULL "
        "AND predicate = 'entity' "
        "AND status != 'superseded'"
    ).bindparams(bindparam("doc_ids", expanding=True))
    rows = session.execute(stmt, {"doc_ids": doc_ids}).fetchall()
    if not rows:
        return empty

    # Group claims by the vertex they resolved to. Two+ claims from DIFFERENT
    # messages sharing one grace_id is exactly the merged-vertex signature.
    claims_by_vertex: dict[str, list[Any]] = {}
    for row in rows:
        claims_by_vertex.setdefault(str(row.resolved_entity_grace_id), []).append(row)

    superseded_ids: set[str] = set()
    pointer_writes: list[tuple[str, str]] = []  # (final_claim_id, earlier_claim_id)
    ambiguous_count = 0
    groups_checked = 0

    for grace_id, claims in claims_by_vertex.items():
        if len(claims) < 2:
            continue
        distinct_docs = {c.source_document_id for c in claims}
        if len(distinct_docs) < 2:
            # Same-message duplicates: no earlier/later ordering — out of scope.
            continue
        groups_checked += 1

        def _order_key(c: Any) -> tuple:
            created = getattr(c, "created_at", None)
            # timestamp() sidesteps naive-vs-aware comparison errors on the tie-break
            return (
                position_by_doc.get(c.source_document_id, 0),
                created.timestamp() if created is not None else float("-inf"),
            )

        # Per-property contradiction scan, ordered by thread position.
        prop_assertions: dict[str, list[Any]] = {}
        for c in sorted(claims, key=_order_key):
            for prop_name, prop_value in (c.properties_json or {}).items():
                prop_assertions.setdefault(prop_name, []).append((c, prop_value))

        for prop_name, assertions in prop_assertions.items():
            if len(assertions) < 2:
                continue
            final_claim, final_value = assertions[-1]
            for earlier_claim, earlier_value in assertions[:-1]:
                if earlier_claim.source_document_id == final_claim.source_document_id:
                    continue  # same message — no ordering
                classification = _classify_value_change(earlier_value, final_value)
                if classification == "contradiction":
                    earlier_id = str(earlier_claim.claim_id)
                    if earlier_id not in superseded_ids:
                        superseded_ids.add(earlier_id)
                        pointer_writes.append((str(final_claim.claim_id), earlier_id))
                    logger.info(
                        "supersession.claim_level_contradiction",
                        thread_id=thread_id,
                        resolved_entity_grace_id=grace_id,
                        property_name=prop_name,
                        superseded_claim_id=earlier_id,
                        superseding_claim_id=str(final_claim.claim_id),
                    )
                elif classification == "ambiguous":
                    ambiguous_count += 1
                    logger.warning(
                        "supersession.claim_level_ambiguous",
                        thread_id=thread_id,
                        resolved_entity_grace_id=grace_id,
                        property_name=prop_name,
                    )

    if dry_run or not superseded_ids:
        return {
            "claims_superseded": len(superseded_ids),
            "pointer_writes": 0,
            "ambiguous_count": ambiguous_count,
            "groups_checked": groups_checked,
        }

    # Writes — mirror the claim_routes.py Edit-and-Accept vocabulary:
    # earlier claim flips to status='superseded'; the superseding (later)
    # claim records supersedes_claim_id → earlier. Pointer is written only
    # when the slot is empty (single-valued column; never overwrite a prior
    # human-set lineage pointer).
    pointer_written = 0
    for earlier_id in superseded_ids:
        session.execute(
            text(
                "UPDATE extraction_claims "
                "SET status = 'superseded', decision_source = 'pipeline' "
                "WHERE claim_id = :claim_id"
            ),
            {"claim_id": earlier_id},
        )
    # Single-valued pointer: when one final claim supersedes several earlier
    # claims, record the MOST RECENT earlier claim (later scan entries win).
    pointer_by_final: dict[str, str] = {f: e for f, e in pointer_writes}
    for final_id, earlier_id in pointer_by_final.items():
        session.execute(
            text(
                "UPDATE extraction_claims "
                "SET supersedes_claim_id = :earlier_id "
                "WHERE claim_id = :final_id AND supersedes_claim_id IS NULL"
            ),
            {"earlier_id": earlier_id, "final_id": final_id},
        )
        pointer_written += 1
    session.commit()

    return {
        "claims_superseded": len(superseded_ids),
        "pointer_writes": pointer_written,
        "ambiguous_count": ambiguous_count,
        "groups_checked": groups_checked,
    }
