"""Graph writer: translates validated claims into ArcadeDB operations.

Bridge between the claim audit trail (PostgreSQL) and the live graph
(ArcadeDB). Handles entity creation, alias appending, relationship
endpoint resolution, relationship creation, and provenance tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.extraction.claim_database import (
    get_extraction_event,
    update_claim_resolved_endpoints,
    update_claim_violations,
)
from src.extraction.claim_models import (
    Claim,
    ClaimStatus,
    ConstraintSeverity,
    ConstraintViolation,
)
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractionBatch
from src.extraction.name_utils import DEFAULT_STRIP_SUFFIXES, normalize_entity_name
from src.extraction.provenance import (
    create_extraction_event_vertex,
    create_produced_by_edges,
    update_event_status_after_write,
)
from src.extraction.temporal_tagger import tag_temporal
from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import (
    build_property_map,
    build_set_clause,
    escape_cypher_string,
)
from src.graph.entity_models import EntityCreate, RelationshipCreate
from src.extraction.entity_resolver import build_embedding_text
from src.graph.entity_ops import (
    append_entity_alias,
    canonical_lookup,
    get_entity,
    insert_entity,
)
from src.ingestion.communications.sensitivity_tagger import tags_from_bar_form, tags_to_bar_form
from src.shared.embeddings import embed_texts
from src.graph.relationship_ops import insert_relationship

log = structlog.get_logger()


# ---------------------------------------------------------------------------
# D465 (Chunk 71 CP3) — Document_Chunk vertex + derives_from edge helpers.
# Invariant: write_batch is the canonical batch writer for extraction.
# Carve-out: chunk-write logic added before per-claim entity writes.
# Authorization: D465.
# ---------------------------------------------------------------------------


def _compute_chunk_sensitivity_tags(text: str) -> str:
    """Compute bar-form sensitivity tags for a chunk via rule-based tagger (D441).

    Conservative posture: tags computed per chunk text content.
    Returns empty string for non-sensitive content.
    """
    tags: list[str] = []
    text_lower = text.lower()
    if any(phrase in text_lower for phrase in (
        "attorney-client", "attorney client", "privileged",
        "work product", "legal privilege",
    )):
        tags.append("privileged")
    # PII density — basic regex detection
    import re
    email_count = len(re.findall(r"\b[\w.+-]+@[\w-]+\.[\w][\w.-]+\b", text))
    phone_count = len(re.findall(r"\b\+?\d[\d\s().-]{7,}\d\b", text))
    ssn_count = len(re.findall(r"\b\d{3}-\d{2}-\d{4}\b", text))
    if (email_count + phone_count + ssn_count) >= 3:
        tags.append("pii_dense")
    return tags_to_bar_form(tags)


async def _insert_document_chunk_vertex(
    client: ArcadeClient,
    grace_id: str,
    source_document_id: str,
    chunk_index: int,
    text: str,
    chunk_token_count: int,
    embedding: list[float],
    sensitivity_tags: str,
) -> str:
    """INSERT a Document_Chunk vertex into ArcadeDB. Returns grace_id.

    Uses OpenCypher CREATE for properties (excluding _embedding),
    then SQL UPDATE for the _embedding LIST property (same pattern as
    entity_ops.insert_entity D445.4).
    """
    props = {
        "grace_id": grace_id,
        "source_document_id": source_document_id,
        "chunk_index": chunk_index,
        "text": text,
        "chunk_token_count": chunk_token_count,
        "extracted_at": datetime.now().isoformat(),
        "sensitivity_tags": sensitivity_tags,
        "_deprecated": False,
    }
    prop_map = build_property_map(props)
    query = f"CREATE (n:Document_Chunk {prop_map}) RETURN n"
    await client.execute_cypher(query)

    # Persist _embedding via SQL UPDATE (LIST type, same pattern as D445.4)
    if embedding:
        embedding_literal = "[" + ",".join(str(v) for v in embedding) + "]"
        escaped_gid = escape_cypher_string(grace_id)
        embed_sql = (
            f"UPDATE Document_Chunk SET _embedding = {embedding_literal} "
            f"WHERE grace_id = '{escaped_gid}'"
        )
        await client.execute_sql(embed_sql)

    return grace_id


async def _lookup_document_chunk(
    client: ArcadeClient,
    source_document_id: str,
    chunk_index: int,
) -> str | None:
    """Canonical lookup for Document_Chunk by (source_document_id, chunk_index).

    Returns grace_id if found, None otherwise.
    """
    escaped_doc = escape_cypher_string(source_document_id)
    query = (
        f"MATCH (n:Document_Chunk {{source_document_id: '{escaped_doc}', "
        f"chunk_index: {chunk_index}}}) RETURN n.grace_id LIMIT 1"
    )
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row.get("n.grace_id") or row.get("grace_id")
    return str(row) if row else None


async def _insert_derives_from_edge(
    client: ArcadeClient,
    entity_grace_id: str,
    chunk_grace_id: str,
) -> None:
    """INSERT a derives_from edge from a Domain entity to a Document_Chunk vertex."""
    from uuid import uuid4 as _uuid4

    edge_grace_id = str(_uuid4())
    escaped_entity = escape_cypher_string(entity_grace_id)
    escaped_chunk = escape_cypher_string(chunk_grace_id)
    query = (
        f"MATCH (e {{grace_id: '{escaped_entity}'}}), "
        f"(c:Document_Chunk {{grace_id: '{escaped_chunk}'}}) "
        f"CREATE (e)-[r:derives_from {{grace_id: '{edge_grace_id}', "
        f"created_at: '{datetime.now().isoformat()}'}}]->(c) RETURN r"
    )
    await client.execute_cypher(query)


# ---------------------------------------------------------------------------
# D501 (Chunk 77b CP5) — Image_Asset vertex + Document_Chunk provenance.
# Invariant: graph_writer is the shared ArcadeDB write helper surface.
# Carve-out: image pipeline imports these helpers; routes MUST NOT import
# image_pipeline (D246). Authorization: D501.
# ---------------------------------------------------------------------------


async def _lookup_image_asset_by_sha256(
    client: ArcadeClient,
    content_sha256: str,
) -> str | None:
    """Return grace_id when an Image_Asset with this content_sha256 exists."""
    escaped = escape_cypher_string(content_sha256)
    query = (
        f"MATCH (n:Image_Asset {{content_sha256: '{escaped}'}}) "
        f"RETURN n.grace_id LIMIT 1"
    )
    result = await client.execute_cypher(query)
    rows = result.get("result", [])
    if not rows:
        return None
    row = rows[0]
    if isinstance(row, dict):
        return row.get("n.grace_id") or row.get("grace_id")
    return str(row) if row else None


async def _insert_image_asset_vertex(
    client: ArcadeClient,
    asset: "ImageAsset",
) -> str:
    """INSERT an Image_Asset vertex. Returns grace_id."""
    from src.extraction.extraction_models import ImageAsset

    if not isinstance(asset, ImageAsset):
        raise TypeError("asset must be ImageAsset")

    extracted_at = asset.extracted_at or datetime.now()
    props = {
        "grace_id": asset.grace_id,
        "source_path": asset.source_path,
        "content_sha256": asset.content_sha256,
        "media_type": asset.media_type,
        "image_class": asset.image_class,
        "ocr_text": asset.ocr_text or "",
        "vision_description_json": asset.vision_description_json or "",
        "sensitivity_tags": asset.sensitivity_tags,
        "extracted_at": extracted_at.isoformat(),
    }
    prop_map = build_property_map(props)
    query = f"CREATE (n:Image_Asset {prop_map}) RETURN n"
    await client.execute_cypher(query)
    return asset.grace_id


async def _insert_derives_from_chunk_to_image(
    client: ArcadeClient,
    chunk_grace_id: str,
    image_grace_id: str,
) -> None:
    """INSERT derives_from edge Document_Chunk → Image_Asset (D501 provenance chain)."""
    from uuid import uuid4 as _uuid4

    edge_grace_id = str(_uuid4())
    escaped_chunk = escape_cypher_string(chunk_grace_id)
    escaped_image = escape_cypher_string(image_grace_id)
    query = (
        f"MATCH (c:Document_Chunk {{grace_id: '{escaped_chunk}'}}), "
        f"(i:Image_Asset {{grace_id: '{escaped_image}'}}) "
        f"CREATE (c)-[r:derives_from {{grace_id: '{edge_grace_id}', "
        f"created_at: '{datetime.now().isoformat()}'}}]->(i) RETURN r"
    )
    await client.execute_cypher(query)


class WriteResult(BaseModel):
    """Result of a graph write batch operation."""

    skipped: bool = Field(default=False, description="True if idempotency check short-circuited")
    reason: str = Field(default="", description="Reason for skip, e.g. 'already_written'")
    entities_created: int = Field(default=0, description="New entities inserted into graph")
    entities_matched: int = Field(default=0, description="Existing entities matched (resolved)")
    entities_failed: int = Field(default=0, description="Entity writes that raised exceptions")
    relationships_created: int = Field(default=0, description="Relationships inserted into graph")
    relationships_failed: int = Field(default=0, description="Relationship writes that raised exceptions")
    aliases_appended: int = Field(default=0, description="Alias variants appended to existing entities")
    produced_by_edges_created: int = Field(default=0, description="Provenance edges created")
    extraction_event_created: bool = Field(default=False, description="Whether Extraction_Event vertex was created")
    chunks_written: int = Field(default=0, description="Document_Chunk vertices created (D465)")
    derives_from_edges_created: int = Field(default=0, description="derives_from edges created (D465)")
    errors: list[dict] = Field(default_factory=list, description="Per-item error details")


_NUM_RE = None


def _coerce_props_to_schema(props: dict, entity_type: str | None, schema: dict) -> dict:
    """Best-effort coercion of LLM string property values to schema data_types.

    F-0018 (validation run, 2026-07-03) — see call site. Only touches
    properties DECLARED in the schema with numeric/datetime/boolean types;
    unknown properties pass through untouched. Coercion failure never drops
    data: the original string moves to `<name>_raw`.

    F-0027 / ISS-0032 — datetime coercion is STRICT (fuzzy=False + ambiguity
    double-parse): only strings that fully determine year/month/day are
    coerced; loose forms ("Q1 2026", "early 2026", bare "2026") route to
    `<name>_raw` instead of inventing a date.
    """
    global _NUM_RE
    import re as _re
    if _NUM_RE is None:
        _NUM_RE = _re.compile(r"-?\d[\d,]*\.?\d*")
    type_def = (schema.get("entity_types") or {}).get(entity_type or "", {})
    declared = type_def.get("properties") or {}
    if not isinstance(declared, dict):
        return props
    out = dict(props)
    for pname, pdef in declared.items():
        if pname not in out or not isinstance(out[pname], str):
            continue
        raw = out[pname].strip()
        dt = (pdef.get("data_type") or "").lower() if isinstance(pdef, dict) else ""
        try:
            if dt in ("float", "double", "number", "decimal"):
                m = _NUM_RE.search(raw.replace("$", ""))
                if not m:
                    raise ValueError(raw)
                out[pname] = float(m.group(0).replace(",", ""))
            elif dt in ("integer", "int", "long"):
                m = _NUM_RE.search(raw.replace("$", ""))
                if not m:
                    raise ValueError(raw)
                out[pname] = int(float(m.group(0).replace(",", "")))
            elif dt in ("datetime", "date"):
                from dateutil import parser as _dtparse
                # F-0027 / ISS-0032 (2026-07-03) — capture-the-why: the original
                # F-0018 coercion used fuzzy=True, which INVENTED a
                # plausible-but-wrong date: a loose LLM value ("Q1 2026")
                # became 2026-01-03T00:00 in-graph while a clean claim carried
                # 2026-03-31. Silent data corruption is worse than the
                # `<prop>_raw` fallback. Strict posture now:
                #   1. fuzzy=False — rejects prose/quarter forms outright
                #      ("Q1 2026", "early 2026", embedded-sentence dates).
                #   2. Double-parse with two different `default` anchors that
                #      differ in year/month/day (times both midnight). dateutil
                #      silently fills missing DATE components from `default`
                #      (e.g. "2026" -> Jan 1, "March" -> current year) — if the
                #      two parses disagree, the string underdetermines the date
                #      and we reject rather than invent. Date-only strings like
                #      "February 20, 2026" agree under both anchors (midnight
                #      time is conventional, not invented) and still parse.
                # Rejection raises -> the except below routes the original
                # string to `<prop>_raw`, preserving F-0018's guarantee that
                # the vertex still writes and no data is dropped.
                _anchor_a = datetime(2001, 1, 1)
                _anchor_b = datetime(2002, 2, 2)
                _parsed_a = _dtparse.parse(raw, fuzzy=False, default=_anchor_a)
                _parsed_b = _dtparse.parse(raw, fuzzy=False, default=_anchor_b)
                if _parsed_a != _parsed_b:
                    raise ValueError(raw)
                out[pname] = _parsed_a.isoformat()
            elif dt in ("boolean", "bool"):
                low = raw.lower()
                if low in ("true", "yes", "1"):
                    out[pname] = True
                elif low in ("false", "no", "0"):
                    out[pname] = False
        except Exception:  # noqa: BLE001 — keep the vertex writable, keep the data
            out[f"{pname}_raw"] = out.pop(pname)
    return out


def _decay_eligibility_stamps(claim: Claim) -> dict:
    """F-0044 / ISS-0033 (2026-07-03) — decay-eligibility triple for extraction writes.

    Capture-the-why: the confidence-decay batch (``confidence_decay.py``
    ``_VERIFIED_QUERY`` / ``_VERIFIED_EDGE_QUERY``) only considers rows where
    ``last_verified_at``, ``confidence_at_verification`` AND ``verdict`` are
    all non-null — but this writer stamped NONE of them (only
    ``extracted_at`` / ``extraction_confidence``), so fresh-graph decay
    coverage was ~0%: the only eligible rows came from the review /
    override writers (D452 stamp sites in ``claim_override_writer.py`` /
    ``graph_review_writer.py``). Stamp the triple additively at extraction
    write time, from what the writer honestly has:

    * ``last_verified_at`` — the write timestamp (same UTC-ISO convention as
      the D452 sites; the verification pass, when run, executes within this
      same batch, so this IS the verification time for verified claims and
      the extraction-observation time for unverified ones).
    * ``confidence_at_verification`` — the claim's extraction confidence
      (the same value written as ``extraction_confidence``). Omitted when
      the claim carries no confidence (never write nulls — fill-only
      discipline), which leaves the row decay-ineligible, honestly.
    * ``verdict`` — the claim's verification verdict VERBATIM
      (``SUPPORTED`` / ``INSUFFICIENT`` / ``PENDING`` when the verify pass
      did not run). We never upgrade to SUPPORTED. Verdicts absent from
      ``decay_config.yaml`` ``verdict_floors`` (e.g. PENDING) get floor 0.0
      in the decay batch — unverified facts decay hardest, which is the
      intended posture; operators can add a PENDING floor in config.

    Merge safety: these keys ride in the ``properties`` dict, so the
    existing fill-only merge discipline applies unchanged — entity canonical
    match (``entity_ops.insert_entity`` F-016) and edge upsert
    (``relationship_ops._upsert_existing_edge`` F-012+F-018) both SET only
    keys the existing row lacks and NEVER overwrite non-null values, so an
    already-stamped vertex/edge (e.g. human-reviewed, D452) is never
    clobbered by a later extraction pass.
    """
    stamps: dict = {
        "last_verified_at": datetime.now(timezone.utc).isoformat(),
    }
    if claim.confidence is not None:
        stamps["confidence_at_verification"] = float(claim.confidence)
    if claim.verdict is not None:
        stamps["verdict"] = claim.verdict.value
    return stamps


# ---------------------------------------------------------------------------
# F-0047b / ISS-0055 Layer 1 (2026-07-03) — sensitivity-tag provenance.
# Capture-the-why: the D520 vertex-global tag union is irreversible and
# provenance-free — one privileged email touching a shared canonical entity
# (a Person, a Property) made it vanish ENTIRELY for restricted principals.
# These helpers stamp, at write time, per-tag source provenance
# (`sensitivity_tag_sources`), a total-write counter
# (`sensitivity_source_total`), and which property names privileged sources
# contributed (`_privileged_props`), so the Layer-2 evidence_scoped posture
# can serve partially-inherited vertices with privileged content scrubbed.
# `sensitivity_tags` union behavior itself is UNCHANGED (CP4/CP5 still read
# it); provenance is additive.
# ---------------------------------------------------------------------------

_SENSITIVITY_SOURCE_ID_CAP = 20


def _parse_json_or(default, raw):
    """Best-effort JSON parse for vertex-carried provenance strings."""
    import json

    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _merge_sensitivity_provenance(
    existing: dict,
    source_document_id: str | None,
    source_tags_bar: str,
    contributed_props: list[str],
) -> dict:
    """Compute the sensitivity-provenance property updates for one write/merge.

    Pure function (ISS-0055 Layer 1). Args:
        existing: current vertex properties (may be ``{}`` for a fresh vertex).
        source_document_id: the contributing write's source document (may be
            None — then neither counter advances; counts stay comparable).
        source_tags_bar: bar-form sensitivity tags the SOURCE carried
            (empty string = clean/untagged source).
        contributed_props: property names this source demonstrably supplies.
            Tagged source -> stamped into ``_privileged_props``.
            Clean source -> REMOVED from ``_privileged_props`` (a clean
            source demonstrably supplying a prop de-privileges it —
            conservative: removal happens only on explicit prop-name match).

    Returns a dict of vertex property updates (JSON strings + int), or ``{}``
    when nothing changed.

    Count semantics (deliberate design refinement over "unique doc ids"):
    per-tag ``count`` increments once per WRITE carrying the tag (ids are
    deduped for display, capped at ``_SENSITIVITY_SOURCE_ID_CAP`` (20) with
    an overflow counter), and ``sensitivity_source_total`` increments once
    per write — BOTH sides of the Layer-2 universal-vs-partial comparison
    count writes, so re-extracting the same privileged document cannot make
    a universally-privileged vertex look partially-inherited.
    """
    import json

    tag_sources: dict = _parse_json_or({}, existing.get("sensitivity_tag_sources"))
    old_total = existing.get("sensitivity_source_total")
    total = int(old_total) if isinstance(old_total, (int, float)) else 0
    priv_props: list = _parse_json_or([], existing.get("_privileged_props"))
    priv_set = {str(p) for p in priv_props}

    old_sources_json = json.dumps(tag_sources, sort_keys=True)
    old_priv = set(priv_set)

    source_tags = tags_from_bar_form(source_tags_bar) if source_tags_bar else []

    if source_document_id:
        total += 1
        for tag in source_tags:
            rec = tag_sources.setdefault(
                tag, {"ids": [], "overflow": 0, "count": 0}
            )
            rec["count"] = int(rec.get("count") or 0) + 1
            ids = rec.setdefault("ids", [])
            if source_document_id not in ids:
                if len(ids) < _SENSITIVITY_SOURCE_ID_CAP:
                    ids.append(source_document_id)
                else:
                    rec["overflow"] = int(rec.get("overflow") or 0) + 1

    if source_tags:
        # Privileged/tagged source: stamp the props it contributed.
        priv_set |= {str(p) for p in contributed_props}
    elif contributed_props:
        # Clean source explicitly carrying these prop names demonstrably
        # supplies them -> de-privilege (conservative removal rule).
        priv_set -= {str(p) for p in contributed_props}

    updates: dict = {}
    new_sources_json = json.dumps(tag_sources, sort_keys=True)
    if new_sources_json != old_sources_json:
        updates["sensitivity_tag_sources"] = new_sources_json
    if source_document_id:
        updates["sensitivity_source_total"] = total
    if priv_set != old_priv:
        updates["_privileged_props"] = json.dumps(sorted(priv_set))
    return updates


async def _apply_sensitivity_provenance(
    client: ArcadeClient,
    grace_id: str,
    existing: dict | None,
    source_document_id: str | None,
    source_tags_bar: str,
    contributed_props: list[str],
) -> None:
    """Fetch-free applier: SET the provenance updates on the vertex.

    Best-effort (log-and-continue): provenance failure must never fail the
    entity write itself — a vertex without provenance fails SAFE at
    enforcement time (Layer 2 treats missing provenance as
    existence-privileged and drops it).
    """
    try:
        updates = _merge_sensitivity_provenance(
            existing or {}, source_document_id, source_tags_bar, contributed_props
        )
        if not updates:
            return
        set_clause = build_set_clause("n", updates)
        await client.execute_cypher(
            f"MATCH (n {{grace_id: '{escape_cypher_string(grace_id)}'}}) "
            f"{set_clause} RETURN n.grace_id"
        )
    except Exception as exc:  # noqa: BLE001 — provenance is additive, never blocking
        log.warning(
            "graph_writer.sensitivity_provenance_failed",
            grace_id=grace_id,
            error=str(exc),
        )


async def _union_edge_sensitivity_tags(
    client: ArcadeClient,
    relationship_type: str,
    source_grace_id: str,
    target_grace_id: str,
    source_tags_bar: str,
) -> None:
    """F-0047b / ISS-0055 Layer 2 — union source tags onto the (upserted) edge.

    Capture-the-why: ``insert_relationship`` upserts on (source, type,
    target) with a fill-only merge, so an existing edge's non-null
    ``sensitivity_tags`` would NOT union with a new tagged source. Mirror
    the D520 vertex most-restrictive-wins union post-write. Best-effort:
    failure logs and continues (the edge itself already carries the new
    source's tags on the CREATE path via the properties map).
    """
    if not source_tags_bar:
        return
    try:
        src_esc = escape_cypher_string(source_grace_id)
        tgt_esc = escape_cypher_string(target_grace_id)
        match = (
            f"MATCH (a {{grace_id: '{src_esc}'}})"
            f"-[r:{relationship_type}]->"
            f"(b {{grace_id: '{tgt_esc}'}}) "
        )
        result = await client.execute_cypher(
            match + "RETURN r.sensitivity_tags AS tags LIMIT 1"
        )
        rows = result.get("result", []) or []
        existing_bar = ""
        if rows and isinstance(rows[0], dict):
            existing_bar = rows[0].get("tags") or ""
        merged = set(tags_from_bar_form(existing_bar)) | set(
            tags_from_bar_form(source_tags_bar)
        )
        merged_bar = tags_to_bar_form(sorted(merged))
        if merged_bar != existing_bar:
            await client.execute_cypher(
                match
                + f"SET r.sensitivity_tags = '{escape_cypher_string(merged_bar)}' "
                + "RETURN r.grace_id"
            )
    except Exception as exc:  # noqa: BLE001 — best-effort union
        log.warning(
            "graph_writer.edge_sensitivity_union_failed",
            relationship_type=relationship_type,
            error=str(exc),
        )


async def write_batch(
    batch: ExtractionBatch,
    schema: dict,
    arcade_client: ArcadeClient,
    session: Session,
    event_id: str,
    config: ExtractionSettings,
    strip_suffixes: list[str] | None = None,
    evidence_origin: Literal["document", "communication", "hybrid"] = "document",
    sensitivity_tags: str = "",
) -> WriteResult:
    """Write validated claims to ArcadeDB as entities and relationships.

    9-step orchestrator:
    1. Idempotency check
    2. Consume pipeline validation/tagging results
    3. Build entity name->grace_id map
    4. Write entities
    5. Resolve relationship endpoints
    6. Write relationships
    7. Create provenance (Extraction_Event + produced_by edges)
    8. Update event status
    9. Return WriteResult
    """
    suffixes = strip_suffixes or DEFAULT_STRIP_SUFFIXES
    result = WriteResult()

    # Step 1: Batch-level idempotency check (D106)
    event = get_extraction_event(session, event_id)
    if event is not None:
        status = event.get("status", "")
        if status in ("graph_written", "completed"):
            return WriteResult(skipped=True, reason="already_written")
        if status == "running":
            raise ValueError(
                f"Event {event_id} status is 'running' — verification incomplete"
            )
        # partial_failed, graph_failed, verified → proceed

    # Step 2: Consume pipeline validation/tagging results (single-owner model)
    # write_batch does NOT call validate_batch or tag_temporal.

    # Step 2.5 (D465): Write Document_Chunk vertices for batch chunks.
    # Build chunk_id -> DocumentChunk map from batch.chunks, then INSERT
    # vertices for unique chunks referenced by claims. Idempotent via
    # canonical lookup on (source_document_id, chunk_index).
    chunk_map: dict[str, object] = {}
    for idx, chunk in enumerate(batch.chunks):
        chunk_map[chunk.chunk_id] = (chunk, idx)

    # Map source_chunk_id -> Document_Chunk grace_id for derives_from edges
    chunk_gid_map: dict[str, str] = {}
    seen_chunks: set[str] = set()

    for claim in batch.claims:
        cid = claim.source_chunk_id
        if not cid or cid in seen_chunks:
            continue
        seen_chunks.add(cid)

        chunk_info = chunk_map.get(cid)
        if chunk_info is None:
            continue
        chunk_obj, chunk_idx = chunk_info

        try:
            # Canonical lookup — idempotent
            existing_gid = await _lookup_document_chunk(
                arcade_client, batch.document_id, chunk_idx,
            )
            if existing_gid:
                chunk_gid_map[cid] = existing_gid
                continue

            # Compute embedding
            ollama_url = config.extraction_base_url or "http://localhost:11434"
            chunk_embedding = (await embed_texts(
                [chunk_obj.text],
                base_url=ollama_url,
                model=config.er_embedding_model,
            ))[0]

            # Compute sensitivity tags
            chunk_sensitivity = _compute_chunk_sensitivity_tags(chunk_obj.text)

            # INSERT vertex
            from uuid import uuid4 as _uuid4
            chunk_grace_id = str(_uuid4())
            await _insert_document_chunk_vertex(
                client=arcade_client,
                grace_id=chunk_grace_id,
                source_document_id=batch.document_id,
                chunk_index=chunk_idx,
                text=chunk_obj.text,
                chunk_token_count=chunk_obj.token_count_estimate,
                embedding=chunk_embedding,
                sensitivity_tags=chunk_sensitivity,
            )
            chunk_gid_map[cid] = chunk_grace_id
            result.chunks_written += 1
        except Exception as exc:
            log.warning(
                "graph_writer.chunk_write_failed",
                source_chunk_id=cid,
                error=str(exc),
            )

    # Step 3: Build entity name→grace_id map from resolved entities
    entity_map: dict[tuple[str, str], str] = {}
    for claim in batch.claims:
        if (
            claim.entity_type
            and claim.resolved_entity_grace_id is not None
            and claim.status != ClaimStatus.QUARANTINED
        ):
            key = (
                normalize_entity_name(claim.subject_name, suffixes),
                claim.entity_type,
            )
            entity_map[key] = claim.resolved_entity_grace_id

    # Build claim_id -> entity/relationship index maps for temporal hints
    entity_temporal: dict[str, tuple[datetime | None, datetime | None]] = {}
    rel_temporal: dict[str, tuple[datetime | None, datetime | None]] = {}

    for entity in batch.entities:
        tagged = tag_temporal(entity.temporal_hints)
        key = (normalize_entity_name(entity.name, suffixes), entity.entity_type)
        # Find matching claim by key
        for claim in batch.claims:
            if (
                claim.entity_type
                and normalize_entity_name(claim.subject_name, suffixes) == key[0]
                and claim.entity_type == key[1]
            ):
                entity_temporal[claim.claim_id] = tagged
                break

    for rel in batch.relationships:
        tagged = tag_temporal(rel.temporal_hints)
        rel_key = (rel.subject_name, rel.predicate, rel.object_name)
        for claim in batch.claims:
            if (
                claim.relationship_type
                and claim.subject_name == rel_key[0]
                and claim.predicate == rel_key[1]
                and claim.object_name == rel_key[2]
            ):
                rel_temporal[claim.claim_id] = tagged
                break

    # Step 4: Write entities
    all_written_gids: list[str] = []

    for claim in batch.claims:
        if not claim.entity_type:
            continue
        if claim.status == ClaimStatus.QUARANTINED:
            continue

        key = (
            normalize_entity_name(claim.subject_name, suffixes),
            claim.entity_type,
        )
        valid_from, valid_to = entity_temporal.get(claim.claim_id, (None, None))

        try:
            if claim.resolved_entity_grace_id is not None:
                # Existing entity — check alias (D103)
                gid = claim.resolved_entity_grace_id
                existing = await get_entity(arcade_client, gid)
                if existing:
                    existing_name = existing.get("name", "")
                    if normalize_entity_name(claim.subject_name, suffixes) != normalize_entity_name(
                        existing_name, suffixes
                    ):
                        appended = await append_entity_alias(
                            arcade_client, gid, claim.subject_name
                        )
                        if appended:
                            result.aliases_appended += 1
                    # D520 — multi-source sensitivity union (most-restrictive-wins).
                    # If new source carries tags, union with existing vertex tags.
                    if sensitivity_tags:
                        existing_tags_str = existing.get("sensitivity_tags", "")
                        existing_tag_set = set(tags_from_bar_form(existing_tags_str))
                        new_tag_set = set(tags_from_bar_form(sensitivity_tags))
                        merged = existing_tag_set | new_tag_set
                        merged_bar = tags_to_bar_form(sorted(merged))
                        if merged_bar != existing_tags_str:
                            from src.graph.cypher_utils import escape_cypher_string as _esc
                            await arcade_client.execute_cypher(
                                f"MATCH (n {{grace_id: '{_esc(gid)}'}}) "
                                f"SET n.sensitivity_tags = '{_esc(merged_bar)}' "
                                f"RETURN n.grace_id"
                            )
                    # F2-07 — multi-source evidence_origin merge (the D520
                    # most-restrictive-wins pattern applied to origin). When
                    # email claims merged into a document-created vertex the
                    # origin stayed 'document', but the corroboration scorer
                    # selects `evidence_origin IN ('communication','hybrid')`
                    # — so the better ER got, the fewer email-corroborated
                    # facts the scorer could see (run-2: the CLOSING-FACT
                    # vertex carried 4 email produced_by edges from 3 distinct
                    # senders and was never scored). Differing origins union
                    # to 'hybrid'.
                    existing_origin = (
                        existing.get("evidence_origin", "document") or "document"
                    )
                    if (
                        evidence_origin != existing_origin
                        and existing_origin != "hybrid"
                    ):
                        from src.graph.cypher_utils import (
                            escape_cypher_string as _esc2,
                        )
                        await arcade_client.execute_cypher(
                            f"MATCH (n {{grace_id: '{_esc2(gid)}'}}) "
                            f"SET n.evidence_origin = 'hybrid' "
                            f"RETURN n.grace_id"
                        )
                    # F-0044 / ISS-0033 — fill-only decay-eligibility stamp on
                    # the ER-resolved merge path. This branch never calls
                    # insert_entity, so entity_ops' fill-only merge cannot
                    # stamp for us. SET only the triple members the vertex
                    # lacks (absent or null); NEVER overwrite — a vertex
                    # already stamped by the review/override writers (D452)
                    # keeps its original verification epoch.
                    _stamps = _decay_eligibility_stamps(claim)
                    _fill = {
                        k: v for k, v in _stamps.items()
                        if existing.get(k) is None
                    }
                    if _fill:
                        _fill_clause = build_set_clause("n", _fill)
                        await arcade_client.execute_cypher(
                            f"MATCH (n {{grace_id: "
                            f"'{escape_cypher_string(gid)}'}}) "
                            f"{_fill_clause} RETURN n.grace_id"
                        )
                    # F-0047b / ISS-0055 Layer 1 — provenance on the
                    # ER-resolved merge path. This branch writes NO domain
                    # properties (only tags/origin/decay stamps), so a
                    # TAGGED source contributes no prop names here
                    # (conservative: nothing was written by it). A CLEAN
                    # source demonstrably carries its claim's prop names ->
                    # they are de-privileged in `_privileged_props`.
                    _claim_prop_names = sorted((claim.properties_json or {}).keys())
                    await _apply_sensitivity_provenance(
                        arcade_client,
                        gid,
                        existing,
                        claim.source_document_id,
                        sensitivity_tags,
                        [] if sensitivity_tags else _claim_prop_names,
                    )
                entity_map[key] = gid
                result.entities_matched += 1
                all_written_gids.append(gid)
            else:
                # New entity
                # D548 — capture-the-why: the vertex `name` property is written
                # SOLELY from EntityCreate.properties["name"] (entity_ops.insert_entity
                # lines 134/162), and canonical dedup also keys on that name. But
                # claim.properties_json can lack "name" when the LLM returns domain
                # fields flat or sparse (weak models — Finding #15), while
                # claim.subject_name ALWAYS carries the canonical name
                # (ExtractedEntity.name is a required field). Without this fallback the
                # vertex persists nameless → undedupable, uncorroboratable,
                # unqueryable-by-name. Mirrors constraint_validator's
                # properties_json.get("name", claim.subject_name) fallback.
                _props = dict(claim.properties_json or {})
                if not _props.get("name") and claim.subject_name:
                    _props["name"] = claim.subject_name
                # F-0018 (validation run, 2026-07-03): LLM property values
                # arrive as display strings ("$2,500,000", "February 20, 2026")
                # while the synced DDL types them DOUBLE/INTEGER/DATETIME —
                # ArcadeDB then rejects the ENTIRE vertex CREATE ("Cannot
                # convert type … to 'DOUBLE'") and the entity is silently lost
                # (Valuation/Credit_Facility/Bid vertices missing; downstream
                # CQs unanswerable). Coerce per the schema's declared
                # data_type; on failure keep the value as `<prop>_raw` so the
                # vertex still writes and no data is dropped.
                _props = _coerce_props_to_schema(_props, claim.entity_type, schema)
                # F-0047b / ISS-0055 Layer 1 — capture the DOMAIN prop names
                # this source supplies BEFORE the infra decay stamps are
                # setdefault'ed in (stamps are infrastructure, not privileged
                # content).
                _domain_prop_names = sorted(_props.keys())
                # F-0044 / ISS-0033 — stamp the decay-eligibility triple
                # (last_verified_at / confidence_at_verification / verdict)
                # so extraction-written vertices are visible to the
                # confidence-decay batch. setdefault: never clobber values
                # already present; insert_entity's canonical-match fill-only
                # merge protects already-stamped existing vertices. See
                # _decay_eligibility_stamps docstring.
                for _sk, _sv in _decay_eligibility_stamps(claim).items():
                    _props.setdefault(_sk, _sv)
                entity_create = EntityCreate(
                    entity_type=claim.entity_type,
                    properties=_props,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    extraction_confidence=claim.confidence,
                    source_document_id=claim.source_document_id,
                    extraction_event_id=claim.extraction_event_id,
                    schema_version=claim.schema_version,
                    ontology_module=claim.ontology_module,
                    evidence_origin=evidence_origin,
                    # D520 — propagate source sensitivity to domain vertices.
                    sensitivity_tags=sensitivity_tags,
                )
                # D445.4 / D356 — embed-on-write; supersedes D89 full-fetch.
                # One embed_texts() call per new entity. Alias updates do NOT
                # trigger re-embed. Authorization: D445.
                embed_text = build_embedding_text(
                    entity_create.properties.get("name", ""),
                    entity_create.entity_type,
                    entity_create.properties,
                )
                ollama_url = config.extraction_base_url or "http://localhost:11434"
                vec = (await embed_texts(
                    [embed_text],
                    base_url=ollama_url,
                    model=config.er_embedding_model,
                ))[0]
                resp = await insert_entity(arcade_client, entity_create, embedding=vec)
                # F-0047b / ISS-0055 Layer 1 — provenance on the new-entity
                # path. Fresh CREATE: seed provenance from this write.
                # Canonical match inside insert_entity (fill-only merge):
                # fetch current vertex state and MERGE provenance on top —
                # a tagged source conservatively stamps ALL its claim props
                # into `_privileged_props` (fill-only means we cannot know
                # post-hoc which subset actually landed; over-marking scrubs
                # more, never less — fail-safe; a later clean source carrying
                # the same prop names de-privileges them). On fetch failure
                # skip the update entirely (stale-but-intact provenance fails
                # toward universal/drop at enforcement — safe direction).
                _prov_existing: dict | None = {}
                if resp.canonical_match:
                    try:
                        _prov_existing = await get_entity(arcade_client, resp.grace_id) or {}
                    except Exception as _prov_exc:  # noqa: BLE001
                        log.warning(
                            "graph_writer.sensitivity_provenance_fetch_failed",
                            grace_id=resp.grace_id,
                            error=str(_prov_exc),
                        )
                        _prov_existing = None
                if _prov_existing is not None:
                    await _apply_sensitivity_provenance(
                        arcade_client,
                        resp.grace_id,
                        _prov_existing,
                        claim.source_document_id,
                        sensitivity_tags,
                        _domain_prop_names,
                    )
                entity_map[key] = resp.grace_id
                result.entities_created += 1
                all_written_gids.append(resp.grace_id)

            # D465: Create derives_from edge from entity to source Document_Chunk
            entity_gid = entity_map.get(key)
            chunk_gid = chunk_gid_map.get(claim.source_chunk_id)
            if entity_gid and chunk_gid:
                try:
                    await _insert_derives_from_edge(
                        arcade_client, entity_gid, chunk_gid,
                    )
                    result.derives_from_edges_created += 1
                except Exception as edge_exc:
                    log.warning(
                        "graph_writer.derives_from_failed",
                        entity_grace_id=entity_gid,
                        chunk_grace_id=chunk_gid,
                        error=str(edge_exc),
                    )
        except Exception as exc:
            result.entities_failed += 1
            error = {
                "error": str(exc),
                "claim_id": claim.claim_id,
                "entity_type": claim.entity_type,
                "name": claim.subject_name,
            }
            result.errors.append(error)
            log.warning(
                "graph_writer.entity_failed",
                claim_id=claim.claim_id,
                error=str(exc),
            )

    # Step 5: Resolve relationship endpoints
    for claim in batch.claims:
        if not claim.relationship_type:
            continue
        if claim.status == ClaimStatus.QUARANTINED:
            continue

        # Resolve subject
        subj_key = (
            normalize_entity_name(claim.subject_name, suffixes),
            claim.subject_type or "",
        )
        subj_gid = entity_map.get(subj_key)
        if subj_gid is None and claim.subject_type:
            subj_gid = await canonical_lookup(
                arcade_client, claim.subject_type, claim.subject_name
            )

        # Resolve object
        obj_key = (
            normalize_entity_name(claim.object_name or "", suffixes),
            claim.object_type or "",
        )
        obj_gid = entity_map.get(obj_key)
        if obj_gid is None and claim.object_type:
            obj_gid = await canonical_lookup(
                arcade_client, claim.object_type, claim.object_name
            )

        if subj_gid is None or obj_gid is None:
            violation = ConstraintViolation(
                severity=ConstraintSeverity.ERROR,
                rule="unresolvable_endpoint",
                message=f"Cannot resolve endpoints: subject={subj_gid is not None}, object={obj_gid is not None}",
            )
            claim.constraint_violations.append(violation)
            claim.status = ClaimStatus.QUARANTINED
            claim.decision_source = "graph_writer"
            update_claim_violations(session, claim.claim_id, claim.constraint_violations, "graph_writer")
            continue

        claim.resolved_subject_grace_id = subj_gid
        claim.resolved_object_grace_id = obj_gid
        update_claim_resolved_endpoints(
            session, claim.claim_id,
            resolved_subject_grace_id=subj_gid,
            resolved_object_grace_id=obj_gid,
        )

    # Step 6: Write relationships
    for claim in batch.claims:
        if not claim.relationship_type:
            continue
        if claim.status == ClaimStatus.QUARANTINED:
            continue
        if not claim.resolved_subject_grace_id or not claim.resolved_object_grace_id:
            continue

        valid_from, valid_to = rel_temporal.get(claim.claim_id, (None, None))

        try:
            # F-0044 / ISS-0033 — stamp the decay-eligibility triple on edges
            # too (the decay batch's _VERIFIED_EDGE_QUERY has the same
            # three-property predicate). setdefault: never clobber; the edge
            # upsert (_upsert_existing_edge) fill-only merge protects
            # already-stamped existing edges. See _decay_eligibility_stamps.
            _rel_props = dict(claim.properties_json or {})
            for _sk, _sv in _decay_eligibility_stamps(claim).items():
                _rel_props.setdefault(_sk, _sv)
            # F-0047b / ISS-0055 Layer 2 — edges inherit source sensitivity
            # tags exactly like vertices (D520 mirror). Without this a
            # relationship claim from a privileged email produced an
            # UNTAGGED edge that would leak the moment its endpoints became
            # visible (evidence_scoped posture). Rides in the properties map
            # because relationship_ops writes all rel.properties verbatim;
            # the upsert-merge union is handled post-write below.
            if sensitivity_tags:
                _rel_props.setdefault("sensitivity_tags", sensitivity_tags)
            rel_create = RelationshipCreate(
                relationship_type=claim.relationship_type,
                source_grace_id=claim.resolved_subject_grace_id,
                target_grace_id=claim.resolved_object_grace_id,
                properties=_rel_props,
                valid_from=valid_from,
                valid_to=valid_to,
                extraction_confidence=claim.confidence,
                source_document_id=claim.source_document_id,
                extraction_event_id=claim.extraction_event_id,
                schema_version=claim.schema_version,
                ontology_module=claim.ontology_module,
            )
            await insert_relationship(arcade_client, rel_create)
            # F-0047b / ISS-0055 Layer 2 — most-restrictive-wins union onto
            # a possibly pre-existing (upserted) edge; fill-only merge alone
            # would not union tags into an edge that already carried some.
            if sensitivity_tags:
                await _union_edge_sensitivity_tags(
                    arcade_client,
                    claim.relationship_type,
                    claim.resolved_subject_grace_id,
                    claim.resolved_object_grace_id,
                    sensitivity_tags,
                )
            result.relationships_created += 1
        except Exception as exc:
            result.relationships_failed += 1
            error = {
                "error": str(exc),
                "claim_id": claim.claim_id,
                "relationship_type": claim.relationship_type,
            }
            result.errors.append(error)
            log.warning(
                "graph_writer.relationship_failed",
                claim_id=claim.claim_id,
                error=str(exc),
            )

    # Step 7: Create provenance
    try:
        event_data = {
            "extraction_event_id": event_id,
            "batch_id": batch.batch_id,
            "source_document_id": batch.document_id,
            "ontology_module": batch.module_name or "",
            "schema_version": batch.schema_version,
            "extractor_model": batch.model_used,
            "verifier_model": "",
            "prompt_template_id": "extraction_v1",
            "avg_confidence": batch.avg_claim_confidence,
            "entities_created": result.entities_created,
            "entities_matched": result.entities_matched,
            "relationships_created": result.relationships_created,
            "claims_accepted": batch.claims_accepted,
            "claims_quarantined": batch.claims_quarantined,
            "started_at": batch.started_at,
            "completed_at": batch.completed_at,
            "status": "graph_written",
        }
        event_gid = await create_extraction_event_vertex(arcade_client, event_data)
        result.extraction_event_created = True

        edges_created = await create_produced_by_edges(
            arcade_client, all_written_gids, event_gid, event_id
        )
        result.produced_by_edges_created = edges_created
    except Exception as exc:
        log.error(
            "graph_writer.provenance_failed",
            event_id=event_id,
            error=str(exc),
        )

    # Step 8: Update event status
    try:
        update_event_status_after_write(session, event_id, result)
    except Exception as exc:
        log.error(
            "graph_writer.status_update_failed",
            event_id=event_id,
            error=str(exc),
        )

    # Step 9: Return result
    return result
