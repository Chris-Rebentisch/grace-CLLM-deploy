"""Duplicate entity detection — finds potential duplicate entities.

Exact name match and embedding-based fuzzy matching. Scoped by entity
type. Uses grace_id comparison to avoid returning both (A,B) and (B,A)
for the same duplicate pair.
"""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
# F-0025b / ISS-0035 capture-the-why: the duplicates report needs the same
# corporate-suffix OCR canonicalization the resolver gained (llo/llg->llc,
# lnc/1nc->inc, c0rp->corp, 1td->ltd, l.l.c->llc), or "Cedar Grove Residences
# LLO" never surfaces next to "... LLC". Import the canonical map instead of
# replicating it — name_utils is a zero-import leaf module (no cycle risk),
# and this file already imports src.extraction.entity_resolver for
# _authoritative_similarity under the same one-implementation rationale
# (relocation of both to src/shared is the documented housekeeping follow-up).
from src.extraction.name_utils import SUFFIX_OCR_VARIANTS
from src.graph.management_models import DuplicateCandidate, DuplicateReport
# D265 / D445.5 — import from src.shared.embeddings (canonical home per D265);
# migrated from src.retrieval.semantic_strategy re-export shim.
from src.shared.embeddings import embed_texts  # noqa: F401 — retained for backcompat

logger = structlog.get_logger()


def _quote_type(type_name: str) -> str:
    """Backtick-quote a type name for SQL/Cypher interpolation.

    F-0003 / ISS-0043 capture-the-why: type names are server-controlled
    (enumerated from ``schema:types``) or operator-supplied; backtick-quote
    them like the fixed ``health_metrics`` pattern so PascalCase_With_
    Underscores and reserved-word types are safe. Embedded backticks are
    stripped rather than escaped — no legitimate type name contains one.
    """
    return f"`{type_name.replace('`', '')}`"


async def _get_vertex_types(client: ArcadeClient) -> list[str]:
    """Get all vertex type names from ArcadeDB schema via SQL.

    F-0003 / ISS-0043 capture-the-why: this previously ran
    ``SELECT DISTINCT @type AS type_name FROM V``. ArcadeDB does not
    auto-create a base ``V`` class in this deployment, so on a V-less
    database ``GET /api/graph/management/duplicates`` 500'd with ``Type
    with name 'V' was not found``. Mirror the fixed
    ``health_metrics.get_health_report()`` pattern: enumerate concrete
    vertex types from ``schema:types``; a schema-less database yields an
    empty list (callers return a clean empty report) with one INFO.
    """
    result = await client.execute_sql("SELECT name, type FROM schema:types")
    names = [
        row["name"]
        for row in result.get("result", [])
        if row.get("type") == "vertex" and row.get("name")
    ]
    if not names:
        logger.info("dedup_detection.schema_not_yet_synced")
    return names


def _canonicalize_name_for_dedup(name: str) -> str:
    """Lowercase + canonicalize a trailing OCR-misread corporate suffix.

    F-0025b / ISS-0035 capture-the-why: OCR scans misread suffix tokens
    (LLC -> LLO/LLG, Inc -> lnc/1nc, ...), so exact-name matching missed
    "Cedar Grove Residences LLO" vs "... LLC". Rewrite ONLY a trailing
    whole token that is a known ``SUFFIX_OCR_VARIANTS`` key to its
    canonical suffix — deliberately NOT full suffix stripping, so
    "Acme LLC" and "Acme Inc" (different companies) never converge.
    Token-boundary by construction (``rsplit`` on whitespace): "Zinc"
    and "Apollo" are single tokens, never mutilated.
    """
    normalized = name.lower().strip().rstrip(",.")
    parts = normalized.rsplit(None, 1)
    if len(parts) == 2:
        prefix, token = parts
        canonical = SUFFIX_OCR_VARIANTS.get(token)
        if canonical is not None:
            return f"{prefix} {canonical}"
    return normalized


async def _detect_duplicates_for_type(
    client: ArcadeClient, entity_type: str,
) -> list[DuplicateCandidate]:
    """Find exact name duplicates within a single entity type."""
    quoted_type = _quote_type(entity_type)
    query = (
        f"MATCH (a:{quoted_type}), (b:{quoted_type}) "
        f"WHERE a.name = b.name AND a.grace_id < b.grace_id "
        f"AND a._deprecated = false AND b._deprecated = false "
        f"RETURN a.grace_id AS a_id, b.grace_id AS b_id, a.name AS name"
    )
    result = await client.execute_cypher(query)
    candidates = []
    for row in result.get("result", []):
        candidates.append(
            DuplicateCandidate(
                entity_a_grace_id=row.get("a_id", ""),
                entity_b_grace_id=row.get("b_id", ""),
                entity_type=entity_type,
                name=row.get("name", ""),
                match_type="exact_name",
            )
        )
    return candidates


async def _detect_suffix_variant_duplicates_for_type(
    client: ArcadeClient, entity_type: str,
) -> list[DuplicateCandidate]:
    """Find suffix-OCR-variant near-duplicates within a single entity type.

    F-0025b / ISS-0035 capture-the-why: client-side pass grouping entity
    names by :func:`_canonicalize_name_for_dedup` so OCR-misread suffix
    pairs ("... LLO" vs "... LLC") surface as duplicate CANDIDATES in the
    report. Pairs whose raw names are identical are skipped — the exact-name
    Cypher pass already reports those. Detection/report only: this module
    (and ``GET /api/graph/management/duplicates``) never writes or merges.
    """
    entities = await _get_entities_for_type(client, entity_type)
    if len(entities) < 2:
        return []

    groups: dict[str, list[tuple[str, str]]] = {}
    for gid, name in entities:
        key = _canonicalize_name_for_dedup(name)
        if key:
            groups.setdefault(key, []).append((gid, name))

    candidates: list[DuplicateCandidate] = []
    for members in groups.values():
        if len(members) < 2:
            continue
        for i, (gid_a, name_a) in enumerate(members):
            for gid_b, name_b in members[i + 1:]:
                if name_a == name_b:
                    # Exact raw-name duplicates belong to the exact_name pass.
                    continue
                a_gid, b_gid = (gid_a, gid_b) if gid_a < gid_b else (gid_b, gid_a)
                a_name = name_a if a_gid == gid_a else name_b
                b_name = name_b if a_gid == gid_a else name_a
                candidates.append(
                    DuplicateCandidate(
                        entity_a_grace_id=a_gid,
                        entity_b_grace_id=b_gid,
                        entity_type=entity_type,
                        name=f"{a_name} / {b_name}",
                        match_type="normalized_name",
                    )
                )
    return candidates


async def detect_duplicates(
    client: ArcadeClient,
    entity_type: str | None = None,
) -> DuplicateReport:
    """Find potential duplicate entities by exact and suffix-variant name match.

    If entity_type is specified, only checks that type.
    If None, queries all vertex types and runs per-type detection.

    Two per-type passes: server-side exact-name Cypher, then the
    client-side suffix-OCR-variant pass (F-0025b / ISS-0035). Report only —
    no write path exists here; merging is a separate, human-gated concern.
    """
    if entity_type:
        types_to_check = [entity_type]
    else:
        types_to_check = await _get_vertex_types(client)

    all_candidates: list[DuplicateCandidate] = []
    by_type: dict[str, int] = {}

    for etype in types_to_check:
        candidates = await _detect_duplicates_for_type(client, etype)
        candidates.extend(
            await _detect_suffix_variant_duplicates_for_type(client, etype)
        )
        if candidates:
            all_candidates.extend(candidates)
            by_type[etype] = len(candidates)

    logger.info(
        "dedup_detection.complete",
        total_candidates=len(all_candidates),
        types_checked=len(types_to_check),
    )

    return DuplicateReport(
        total_candidates=len(all_candidates),
        by_type=by_type,
        candidates=all_candidates,
    )


async def _get_entities_for_type(
    client: ArcadeClient, entity_type: str,
) -> list[tuple[str, str]]:
    """Get all non-deprecated entities (grace_id, name) for a type."""
    # F-0003 / ISS-0043 rider: backtick-quote the type name (see _quote_type).
    query = (
        f"MATCH (n:{_quote_type(entity_type)}) WHERE n._deprecated = false "
        f"RETURN n.grace_id, n.name"
    )
    result = await client.execute_cypher(query)
    entities = []
    for row in result.get("result", []):
        gid = row.get("n.grace_id") or row.get("grace_id", "")
        name = row.get("n.name") or row.get("name", "")
        if gid and name:
            entities.append((gid, name))
    return entities


async def detect_fuzzy_duplicates(
    client: ArcadeClient,
    entity_type: str | None = None,
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    similarity_threshold: float = 0.85,
    ann_top_k: int = 20,
) -> DuplicateReport:
    """Find fuzzy duplicates using per-entity vectorNeighbors() ANN queries.

    # D445.5 / D356 — replaces O(n²) pairwise numpy sweep with per-entity
    # ANN queries against the persisted _embedding property. Authorization: D445.5.

    For each non-deprecated entity of a type, issues a vectorNeighbors()
    SQL query using the entity's persisted _embedding. Pairs above the
    similarity_threshold with grace_id[a] < grace_id[b] ordering are
    collected as DuplicateCandidate.

    Args:
        client: ArcadeDB client.
        entity_type: If specified, only check this type. If None, all types.
        ollama_base_url: Ollama endpoint (unused — embeddings are persisted).
        embedding_model: Model id (unused — embeddings are persisted).
        similarity_threshold: Minimum cosine similarity to report.
        ann_top_k: Number of ANN neighbors to fetch per entity.
    """
    if entity_type:
        types_to_check = [entity_type]
    else:
        types_to_check = await _get_vertex_types(client)

    all_candidates: list[DuplicateCandidate] = []
    by_type: dict[str, int] = {}

    for etype in types_to_check:
        entities = await _get_entities_for_type(client, etype)

        if len(entities) < 2:
            continue

        # For each entity, query ANN neighbors via persisted _embedding
        seen_pairs: set[tuple[str, str]] = set()
        type_candidates: list[DuplicateCandidate] = []

        for gid, name in entities:
            # Fetch the entity's persisted embedding
            # F-0003 / ISS-0043 rider: backtick-quote the type name.
            embed_result = await client.execute_sql(
                f"SELECT _embedding FROM {_quote_type(etype)} "
                f"WHERE grace_id = '{escape_cypher_string(gid)}'"
            )
            embed_rows = embed_result.get("result", [])
            if not embed_rows or not embed_rows[0].get("_embedding"):
                continue

            embedding = embed_rows[0]["_embedding"]
            embedding_literal = "[" + ",".join(str(v) for v in embedding) + "]"

            # D445.5 — SQL ANN query via vectorNeighbors(); SQL convention
            # carve-out same as CP5. Authorization: D445.5.
            ann_sql = (
                f"SELECT vectorNeighbors('{etype}[_embedding]', "
                f"{embedding_literal}, {ann_top_k}) AS neighbors"
            )
            try:
                ann_result = await client.execute_sql(ann_sql)
            except Exception as exc:
                logger.warning(
                    "dedup_detection.ann_failed",
                    entity_type=etype,
                    grace_id=gid,
                    error=str(exc),
                )
                continue

            ann_rows = ann_result.get("result", [])
            if not ann_rows or not ann_rows[0].get("neighbors"):
                continue

            for neighbor in ann_rows[0]["neighbors"]:
                n_gid = neighbor.get("grace_id", "")
                n_name = neighbor.get("name", "")
                n_deprecated = neighbor.get("_deprecated", False)
                # F2-03 sweep: this was the last remaining raw
                # `1.0 - distance` ANN site — the exact F-08 pattern
                # (vectorNeighbors returns distance 0.0 for stale vectors ->
                # "similarity 1.00" -> false duplicate candidates). Route
                # through the authoritative helper: client-side cosine from
                # the neighbor's own _embedding when present; distance-0.0
                # accepted only on exact-name match; None -> drop candidate.
                # Cross-module import of the private helper is deliberate —
                # ONE implementation of this safety logic (relocation to
                # src/shared is a housekeeping follow-up, same rationale as
                # _parse_json_robust in CLAUDE.md).
                from src.extraction.entity_resolver import (
                    _authoritative_similarity,
                )

                similarity = _authoritative_similarity(embedding, neighbor, name)
                if similarity is None:
                    continue

                if not n_gid or n_gid == gid or n_deprecated:
                    continue
                if similarity < similarity_threshold:
                    continue

                # Ensure grace_id ordering and dedup
                pair = (min(gid, n_gid), max(gid, n_gid))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                a_gid, b_gid = pair
                a_name = name if a_gid == gid else n_name
                b_name = n_name if b_gid == n_gid else name
                type_candidates.append(
                    DuplicateCandidate(
                        entity_a_grace_id=a_gid,
                        entity_b_grace_id=b_gid,
                        entity_type=etype,
                        name=f"{a_name} / {b_name}",
                        match_type="embedding_similarity",
                        similarity_score=similarity,
                    )
                )

        if type_candidates:
            all_candidates.extend(type_candidates)
            by_type[etype] = len(type_candidates)

    logger.info(
        "dedup_detection.fuzzy_complete",
        total_candidates=len(all_candidates),
        types_checked=len(types_to_check),
    )

    return DuplicateReport(
        total_candidates=len(all_candidates),
        by_type=by_type,
        candidates=all_candidates,
    )
