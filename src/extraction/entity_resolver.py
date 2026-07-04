"""Three-tier entity resolution: exact -> embedding -> LLM.

Determines whether an extracted entity is new or matches an existing
entity in the graph. Every resolution decision is logged.

# NOTE: Extraction depends on retrieval helpers: embed_texts,
# cosine_similarity (semantic_strategy), BM25SearchIndex (bm25_strategy).
# Read-only — no shared state. Phase 5: optional move to src/shared/embeddings.py.
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel, Field

from src.extraction.entity_registry import EntityRegistry
from src.extraction.extraction_config import ExtractionSettings
from src.extraction.extraction_models import ExtractedEntity
from src.extraction.instructor_client import ExtractionLLMClient, ExtractionLLMError
from src.extraction.name_utils import (
    DEFAULT_STRIP_SUFFIXES,
    expand_suffix_ocr_variants,
    normalize_entity_name,
)
from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.graph.entity_ops import append_entity_alias, canonical_lookup
from src.shared.embeddings import embed_texts

log = structlog.get_logger()

# F-08 — treat an ANN cosine distance at or below this as the untrustworthy
# "un-reindexed vector" sentinel that ArcadeDB's LSMVectorIndex returns as 0.0.
_ANN_DISTANCE_ZERO_EPS = 1e-9

# F-0032d / ISS-0035 — entity types eligible for the single-token
# person-name-fragment adjudication check (email extraction re-emits bare
# first names like "Theodore" as new Person vertices).
PERSON_ENTITY_TYPES: frozenset[str] = frozenset({"Person"})


def _merge_notes(*notes: str | None) -> str | None:
    """Join non-empty resolution notes with ';', capped to the 200-char DB column.

    ISS-0035: lets a Tier-1.5 sentinel note (e.g. suffix_ocr_variant_candidate)
    survive alongside llm_disambiguation_failed instead of being overwritten.
    """
    merged = ";".join(n for n in notes if n)
    return merged[:200] if merged else None


def _pure_cosine(a: list[float], b: list[float]) -> float | None:
    """Pure-python cosine similarity; None if either vector is empty/zero-norm."""
    if not a or not b or len(a) != len(b):
        return None
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return None
    return dot / ((na**0.5) * (nb**0.5))


def _authoritative_similarity(
    query_vec: list[float], neighbor: dict, extracted_name: str
) -> float | None:
    """Return a trustworthy similarity for an ANN neighbor (F-08).

    ArcadeDB's ``vectorNeighbors()`` returns ``distance: 0.0`` for stale /
    un-reindexed vectors, which the old code turned into ``similarity = 1.0``
    and auto-merged unrelated entities. Strategy:

    1. If the neighbor carries its own ``_embedding``, recompute the cosine
       client-side and use that (authoritative — ignores the ANN distance).
    2. Otherwise, if the ANN distance is the untrustworthy 0.0 sentinel, only
       accept it when the names match exactly (case-insensitive); else return
       ``None`` so the caller drops the candidate (conservative false-merge bias).
    3. Otherwise fall back to ``1.0 - distance`` (a non-zero distance is a real
       index result).
    """
    emb = neighbor.get("_embedding")
    if isinstance(emb, list) and emb:
        sim = _pure_cosine(query_vec, emb)
        if sim is not None:
            return sim
    distance = neighbor.get("distance", 1.0)
    if distance <= _ANN_DISTANCE_ZERO_EPS:
        name = (neighbor.get("name") or "").strip().lower()
        if name and name == (extracted_name or "").strip().lower():
            return 1.0  # exact-name match is trustworthy even at distance 0.0
        return None  # untrustworthy distance-0.0 with a different name
    return 1.0 - distance


class DisambiguationResult(BaseModel):
    """Instructor response_model for Tier 3 LLM disambiguation."""

    decision: Literal["YES", "NO"]
    reasoning: str = Field(
        description="Brief explanation of why these are or are not the same entity"
    )


class EntityResolutionResult(BaseModel):
    """Result of resolving a single extracted entity."""

    extracted_name: str
    extracted_type: str
    resolved_grace_id: str | None = None  # None = new entity
    matched_name: str | None = None
    resolution_tier: str  # "exact", "embedding", "llm", "new"
    similarity_score: float | None = None
    blocking_key: str  # e.g., "type:Legal_Entity"
    is_new: bool
    candidate_count: int = 0
    candidates_json: list[dict] | None = None
    llm_reasoning: str | None = None
    resolution_note: str | None = None  # e.g., "llm_disambiguation_failed"


def build_embedding_text(
    name: str, entity_type: str, properties: dict | None
) -> str:
    """Build text for embedding. D88: name + type + description only."""
    base = f"{name} ({entity_type})"
    if properties and "description" in properties:
        desc = str(properties["description"])[:200]
        return f"{base}: {desc}"
    return base


class EntityResolver:
    """Three-tier entity resolution: exact -> embedding -> LLM.

    Tier 1 -- Exact name match: normalized string comparison via
    canonical_lookup in entity_ops.py. Free, instant, deterministic.

    Tier 2 -- Semantic embedding similarity: BM25 candidate narrowing
    (top-K from same entity type) then nomic-embed-text cosine similarity.
    Per-type merge/review thresholds.

    Tier 3 -- LLM disambiguation: short Instructor call for ambiguous
    Tier 2 results (similarity between review and merge thresholds).
    Only triggered for genuinely ambiguous cases.
    """

    def __init__(
        self,
        arcade_client: ArcadeClient,
        config: ExtractionSettings,
        ollama_base_url: str = "http://localhost:11434",
        instructor_client: ExtractionLLMClient | None = None,
        strip_suffixes: list[str] | None = None,
    ) -> None:
        """Initialize resolver.

        Args:
            arcade_client: ArcadeDB client for graph queries.
            config: Extraction settings with ER thresholds.
            ollama_base_url: Ollama endpoint for Tier 2 embeddings.
            instructor_client: LLM client for Tier 3 disambiguation.
            strip_suffixes: Same list as ExtractionPipeline._strip_suffixes
                (defaults to name_utils.DEFAULT_STRIP_SUFFIXES). Used for Tier 1
                normalization and for EntityRegistry cache keys (must match dedup).
        """
        self._arcade_client = arcade_client
        self._config = config
        self._ollama_base_url = ollama_base_url
        self._instructor_client = instructor_client
        self._strip_suffixes = strip_suffixes or DEFAULT_STRIP_SUFFIXES

    async def resolve_entity(
        self,
        entity: ExtractedEntity,
        registry: EntityRegistry,
        extraction_event_id: str | None = None,
    ) -> EntityResolutionResult:
        """Resolve a single entity against the graph.

        Args:
            entity: Extracted entity to resolve.
            registry: Batch-scoped cache; mutated by this method.
            extraction_event_id: For logging correlation.

        Returns:
            EntityResolutionResult with resolution tier and match info.
        """
        # Check cache first
        cached = registry.get(entity.name, entity.entity_type)
        if cached is not None:
            return cached

        blocking_key = f"type:{entity.entity_type}"

        # Tier 1 — Exact match
        result = await self._tier1_exact(entity, blocking_key)
        if result is not None:
            return await self._finalize(entity, registry, result)

        # Tier 1.5 — conservative candidate routing (F-0025b / F-0032d,
        # ISS-0035): suffix-OCR variants and single-token Person fragments
        # produce a Tier-3 adjudication sentinel. They NEVER merge directly
        # and NEVER loosen Tier-2 thresholds — the LLM (or, absent a client,
        # the existing conservative mint-with-note fallback) decides.
        top_candidate = await self._suffix_variant_candidate(entity, blocking_key)
        if top_candidate is None:
            top_candidate = await self._person_fragment_candidate(entity, blocking_key)

        if top_candidate is None:
            # Tier 2 — Embedding similarity
            result = await self._tier2_embedding(entity, blocking_key, registry)
            if result is None:
                # Contract violation (tier2 documents it never returns None) —
                # conservative false-merge bias: treat as new.
                result = EntityResolutionResult(
                    extracted_name=entity.name,
                    extracted_type=entity.entity_type,
                    resolution_tier="new",
                    blocking_key=blocking_key,
                    is_new=True,
                )
            if result.is_new or result.resolution_tier != "_tier3":
                return await self._finalize(entity, registry, result)
            # Tier 3 needed — result carries top candidate info
            top_candidate = result

        # Tier 3 — LLM disambiguation
        result = await self._tier3_llm(entity, top_candidate, blocking_key)
        return await self._finalize(entity, registry, result)

    async def _finalize(
        self,
        entity: ExtractedEntity,
        registry: EntityRegistry,
        result: EntityResolutionResult,
    ) -> EntityResolutionResult:
        """Cache + post-checks for a completed resolution result.

        F-0041 / ISS-0034: newly-minted entities get a cross-type
        name-collision visibility check before caching. Matched (is_new=False)
        results pass through untouched.
        """
        if result.is_new:
            await self._flag_cross_type_collision(entity, result)
        registry.put(entity.name, entity.entity_type, result)
        return result

    async def resolve_batch(
        self,
        entities: list[ExtractedEntity],
        extraction_event_id: str | None = None,
    ) -> list[EntityResolutionResult]:
        """Resolve a batch of entities sequentially (D90).

        Creates EntityRegistry, calls resolve_entity for each entity
        in order, clears registry at end.
        """
        registry = EntityRegistry(strip_suffixes=self._strip_suffixes)
        results: list[EntityResolutionResult] = []

        for entity in entities:
            result = await self.resolve_entity(entity, registry, extraction_event_id)
            results.append(result)

        registry.clear()
        return results

    async def _tier1_exact(
        self, entity: ExtractedEntity, blocking_key: str
    ) -> EntityResolutionResult | None:
        """Tier 1: Exact name match via canonical_lookup."""
        normalized = normalize_entity_name(entity.name, self._strip_suffixes)

        # Original name first (as extracted)
        grace_id = await canonical_lookup(
            self._arcade_client, entity.entity_type, entity.name
        )
        matched_name = entity.name

        # Normalized name second when first misses — required when graph stores
        # lowercase/canonical form but extraction preserves casing (e.g. graph
        # "acme" vs extracted "ACME"). Using `!= entity.name.lower().strip()` was
        # wrong: that equals `normalized` only for case change, not suffix strip,
        # and skipped the case-only case when lower().strip() == normalized.
        if grace_id is None:
            grace_id = await canonical_lookup(
                self._arcade_client, entity.entity_type, normalized
            )
            if grace_id is not None:
                matched_name = normalized

        if grace_id is not None:
            await self._persist_alias_if_variant(
                grace_id=grace_id,
                extracted_name=entity.name,
                matched_name=matched_name,
            )
            log.info(
                "entity_resolution.tier1_match",
                name=entity.name,
                grace_id=grace_id,
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolved_grace_id=grace_id,
                matched_name=matched_name,
                resolution_tier="exact",
                blocking_key=blocking_key,
                is_new=False,
            )

        return None

    async def _suffix_variant_candidate(
        self, entity: ExtractedEntity, blocking_key: str
    ) -> EntityResolutionResult | None:
        """Tier 1.5a — OCR suffix-variant candidate (F-0025b / ISS-0035).

        capture-the-why (F-0025b / ISS-0035): "Cedar Grove Residences LLO"
        (a scan misread of LLC) minted a phantom near-duplicate because
        neither Tier-1 form matched the stored "...LLC" vertex. When the
        trailing token is a known OCR misread of a corporate suffix, look up
        the canonical respelling; a hit becomes a Tier-3 adjudication
        sentinel. Conservative by construction: no silent merge, no Tier-2
        threshold change — the LLM (or the mint-with-note fallback when no
        client is configured) makes the call.
        """
        for variant_name in expand_suffix_ocr_variants(entity.name):
            try:
                grace_id = await canonical_lookup(
                    self._arcade_client, entity.entity_type, variant_name
                )
            except Exception:
                log.warning(
                    "entity_resolution.suffix_variant_lookup_failed",
                    name=entity.name,
                    variant=variant_name,
                    exc_info=True,
                )
                continue
            if grace_id:
                log.info(
                    "entity_resolution.suffix_variant_candidate",
                    name=entity.name,
                    variant=variant_name,
                    grace_id=grace_id,
                )
                return EntityResolutionResult(
                    extracted_name=entity.name,
                    extracted_type=entity.entity_type,
                    resolved_grace_id=grace_id,
                    matched_name=variant_name,
                    resolution_tier="_tier3",  # sentinel — Tier 3 adjudicates
                    similarity_score=None,
                    blocking_key=blocking_key,
                    is_new=False,
                    candidate_count=1,
                    candidates_json=[
                        {
                            "grace_id": grace_id,
                            "name": variant_name,
                            "source": "suffix_ocr_variant",
                        }
                    ],
                    resolution_note="suffix_ocr_variant_candidate",
                )
        return None

    async def _person_fragment_candidate(
        self, entity: ExtractedEntity, blocking_key: str
    ) -> EntityResolutionResult | None:
        """Tier 1.5b — single-token Person fragment (F-0032d / ISS-0035).

        capture-the-why (F-0032d / ISS-0035): email-path extraction re-emits
        bare first names ("Theodore", "Edward", "Marcus", "Dana") as NEW
        Person vertices even when a Person with the matching full name
        exists. When a new Person's name is a single token that
        case-insensitively matches the first or last token of exactly ONE
        existing Person's name, route to Tier-3 adjudication instead of
        minting. Zero or multiple matches mint as today (debug log only) —
        guessing among multiple full-name owners is exactly the over-eager
        merge class that burned this project twice.
        """
        if entity.entity_type not in PERSON_ENTITY_TYPES:
            return None
        token = entity.name.strip()
        if not token or len(token.split()) != 1:
            return None
        escaped = escape_cypher_string(token.lower())
        query = (
            f"MATCH (n:{entity.entity_type}) "
            f"WHERE n.name IS NOT NULL "
            f"AND (toLower(n.name) STARTS WITH '{escaped} ' "
            f"OR toLower(n.name) ENDS WITH ' {escaped}') "
            f"RETURN n.grace_id AS grace_id, n.name AS name LIMIT 5"
        )
        try:
            result = await self._arcade_client.execute_cypher(query)
        except Exception:
            log.warning(
                "entity_resolution.person_fragment_lookup_failed",
                name=entity.name,
                exc_info=True,
            )
            return None
        matches: list[tuple[str, str]] = []
        for row in result.get("result", []):
            if not isinstance(row, dict):
                continue
            gid = row.get("grace_id") or row.get("n.grace_id")
            name = row.get("name") or row.get("n.name")
            if gid and name:
                matches.append((gid, name))
        if len(matches) == 1:
            gid, name = matches[0]
            log.info(
                "entity_resolution.person_fragment_candidate",
                name=entity.name,
                matched=name,
                grace_id=gid,
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolved_grace_id=gid,
                matched_name=name,
                resolution_tier="_tier3",  # sentinel — Tier 3 adjudicates
                similarity_score=None,
                blocking_key=blocking_key,
                is_new=False,
                candidate_count=1,
                candidates_json=[
                    {
                        "grace_id": gid,
                        "name": name,
                        "source": "person_name_fragment",
                    }
                ],
                resolution_note="person_name_fragment_candidate",
            )
        log.debug(
            "entity_resolution.person_fragment_not_unique",
            name=entity.name,
            match_count=len(matches),
        )
        return None

    async def _flag_cross_type_collision(
        self, entity: ExtractedEntity, result: EntityResolutionResult
    ) -> None:
        """Flag same-normalized-name entities of a DIFFERENT type (F-0041 / ISS-0034).

        capture-the-why (F-0041 / ISS-0034): "Fairview County Water District" was
        extracted as BOTH Legal_Entity (conf 0.3) and Vendor (conf 0.8) — two
        vertices for one real-world entity. The (type, name) dedup key and the
        per-type ANN blocking never look across types, so the duplicate was
        invisible until a human reviewer tripped over it. This check makes the
        collision VISIBLE and adjudicable without blocking creation: structlog
        warning + resolution-log row flagged ``cross_type_name_collision``
        (the note also lands on the entity claims via
        _apply_resolution_to_claims), with the colliding vertices appended to
        candidates_json. Deliberately NOT an auto-merge — cross-type identity
        is exactly the ambiguous-evidence class that burned this project
        twice (0.0-distance auto-merges; sibling-LLC 0.9096 merge); a human
        adjudicates. Failures are log-and-continue and never affect the
        resolution outcome.

        SQL (not OpenCypher) carve-out: the type-agnostic scan needs ``@type``
        projection over the V base class, which OpenCypher cannot express —
        same authorization pattern as the D445.3 vectorNeighbors() SQL
        carve-out in _tier2_embedding. Authorization: F-0041 / ISS-0034.

        Documented limitation: matches on exact lowercase name (raw or
        normalized form) only; a cross-type duplicate whose stored name
        carries a different corporate suffix is not detected in v1.
        """
        normalized = normalize_entity_name(entity.name, self._strip_suffixes)
        if not normalized:
            return
        names = {normalized, entity.name.strip().lower()}
        in_list = ", ".join(
            f"'{escape_cypher_string(n)}'" for n in sorted(names) if n
        )
        sql = (
            "SELECT @type AS entity_type, grace_id, name FROM V "
            f"WHERE name IS NOT NULL AND name.toLowerCase() IN [{in_list}] "
            "LIMIT 10"
        )
        try:
            res = await self._arcade_client.execute_sql(sql)
        except Exception:
            # Visibility check must never break resolution — log and continue.
            log.warning(
                "entity_resolution.cross_type_check_failed",
                name=entity.name,
                entity_type=entity.entity_type,
                exc_info=True,
            )
            return
        collisions: list[dict] = []
        for row in res.get("result", []):
            if not isinstance(row, dict):
                continue
            row_type = row.get("entity_type") or row.get("@type")
            gid = row.get("grace_id")
            name = row.get("name") or ""
            if not gid or not row_type or row_type == entity.entity_type:
                continue
            if normalize_entity_name(name, self._strip_suffixes) != normalized:
                continue
            collisions.append(
                {
                    "grace_id": gid,
                    "name": name,
                    "entity_type": row_type,
                    "flag": "cross_type_name_collision",
                }
            )
        if not collisions:
            return
        log.warning(
            "entity_resolution.cross_type_name_collision",
            name=entity.name,
            entity_type=entity.entity_type,
            collisions=collisions,
        )
        note = "cross_type_name_collision"
        result.resolution_note = (
            f"{result.resolution_note};{note}"[:200]
            if result.resolution_note
            else note
        )
        result.candidates_json = (result.candidates_json or []) + collisions

    async def _tier2_embedding(
        self,
        entity: ExtractedEntity,
        blocking_key: str,
        registry: EntityRegistry,
    ) -> EntityResolutionResult | None:
        """Tier 2: Embedding similarity via ArcadeDB vectorNeighbors() ANN.

        # D445.3 / D356 — Tier-2 ANN via vectorNeighbors(); supersedes D89
        # full-fetch. SQL convention carve-out (ArcadeDB does not support
        # vector search in OpenCypher). Authorization: D445.3.

        Returns:
            - EntityResolutionResult with is_new=True if new entity
            - EntityResolutionResult with is_new=False if auto-merge
            - EntityResolutionResult with resolution_tier="_tier3" if needs LLM (sentinel)
            - None should not happen
        """
        # Embed the extracted entity
        entity_text = build_embedding_text(
            entity.name, entity.entity_type, entity.properties
        )
        entity_vec = await embed_texts(
            [entity_text],
            base_url=self._ollama_base_url,
            model=self._config.er_embedding_model,
        )
        query_vec = entity_vec[0]

        # D445.3 — SQL ANN query via vectorNeighbors(). Index naming convention
        # resolved at CP2 build-time gate: '{Type}[_embedding]'.
        # vectorNeighbors() returns an array of neighbor objects with .distance
        # (COSINE distance, lower=better). Similarity = 1.0 - distance.
        embedding_literal = "[" + ",".join(str(v) for v in query_vec) + "]"
        top_k = self._config.er_ann_top_k
        ann_sql = (
            f"SELECT vectorNeighbors('{entity.entity_type}[_embedding]', "
            f"{embedding_literal}, {top_k}) AS neighbors"
        )
        try:
            result = await self._arcade_client.execute_sql(ann_sql)
        except Exception as exc:
            log.warning(
                "entity_resolution.ann_query_failed",
                entity_type=entity.entity_type,
                error=str(exc),
            )
            # D86 conservative false-merge bias: on ANN failure, treat as new
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolution_tier="new",
                blocking_key=blocking_key,
                is_new=True,
            )

        rows = result.get("result", [])
        if not rows or not rows[0].get("neighbors"):
            log.info(
                "entity_resolution.no_candidates",
                name=entity.name,
                entity_type=entity.entity_type,
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolution_tier="new",
                blocking_key=blocking_key,
                is_new=True,
            )

        # Parse ANN neighbors — filter deprecated entities post-query
        neighbors = rows[0]["neighbors"]
        candidates: list[tuple[str, str, float]] = []  # (grace_id, name, similarity)
        for neighbor in neighbors:
            gid = neighbor.get("grace_id", "")
            name = neighbor.get("name", "")
            deprecated = neighbor.get("_deprecated", False)
            if not (gid and name) or deprecated:
                continue
            # F-08 (validation run, 2026-07-01) capture-the-why:
            # ArcadeDB's LSMVectorIndex serves a stale/un-reindexed vector with
            # `distance: 0.0` to ANY query (because `_embedding` is written by
            # SQL UPDATE *after* vertex CREATE, so the index hasn't ingested it).
            # `similarity = 1.0 - distance` therefore auto-merged UNRELATED
            # entities at "sim 1.00" (11+ catastrophic false merges observed,
            # e.g. an unrelated company merged into a sibling family LLC and
            # an unrelated person merged into a family principal). Never
            # trust the ANN distance: the neighbor object
            # carries its own `_embedding`, so recompute the true cosine
            # client-side. Only fall back to the ANN distance when the neighbor
            # has no usable embedding AND either the names match exactly or the
            # distance is not the untrustworthy 0.0 sentinel.
            similarity = _authoritative_similarity(
                query_vec, neighbor, extracted_name=entity.name
            )
            if similarity is None:
                # Untrustworthy distance-0.0 with a non-matching name and no
                # embedding to verify — drop (D86 conservative false-merge bias).
                log.warning(
                    "entity_resolution.ann_distance_zero_unverifiable",
                    name=entity.name,
                    candidate=name,
                    entity_type=entity.entity_type,
                )
                continue
            candidates.append((gid, name, similarity))

        if not candidates:
            log.info(
                "entity_resolution.no_candidates",
                name=entity.name,
                entity_type=entity.entity_type,
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolution_tier="new",
                blocking_key=blocking_key,
                is_new=True,
            )

        # Build candidates_json
        candidates_json = [
            {"grace_id": gid, "name": name, "score": score}
            for gid, name, score in candidates
        ]
        candidates_json.sort(key=lambda c: c["score"], reverse=True)

        # D95 argmax with lexicographic grace_id tiebreak
        max_score = max(score for _, _, score in candidates)
        max_candidates = [
            (gid, name) for gid, name, score in candidates
            if score == max_score
        ]
        if len(max_candidates) > 1:
            best_gid, best_name = min(max_candidates, key=lambda c: c[0])
        else:
            best_gid, best_name = max_candidates[0]

        # Get per-type thresholds
        type_thresholds = self._config.er_thresholds.get(
            entity.entity_type,
            {"merge": self._config.er_default_merge, "review": self._config.er_default_review},
        )
        merge_threshold = type_thresholds["merge"]
        review_threshold = type_thresholds["review"]

        if max_score >= merge_threshold:
            await self._persist_alias_if_variant(
                grace_id=best_gid,
                extracted_name=entity.name,
                matched_name=best_name,
            )
            log.info(
                "entity_resolution.tier2_merge",
                name=entity.name,
                matched=best_name,
                score=max_score,
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolved_grace_id=best_gid,
                matched_name=best_name,
                resolution_tier="embedding",
                similarity_score=max_score,
                blocking_key=blocking_key,
                is_new=False,
                candidate_count=len(candidates),
                candidates_json=candidates_json,
            )

        if max_score >= review_threshold:
            # Needs Tier 3 — return sentinel with candidate info
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolved_grace_id=best_gid,
                matched_name=best_name,
                resolution_tier="_tier3",  # sentinel — will be replaced
                similarity_score=max_score,
                blocking_key=blocking_key,
                is_new=False,
                candidate_count=len(candidates),
                candidates_json=candidates_json,
            )

        # Below review threshold — new entity (D86 conservative false-merge bias)
        log.info(
            "entity_resolution.tier2_new",
            name=entity.name,
            best_score=max_score,
            threshold=review_threshold,
        )
        return EntityResolutionResult(
            extracted_name=entity.name,
            extracted_type=entity.entity_type,
            resolution_tier="new",
            similarity_score=max_score,
            blocking_key=blocking_key,
            is_new=True,
            candidate_count=len(candidates),
            candidates_json=candidates_json,
        )

    async def _tier3_llm(
        self,
        entity: ExtractedEntity,
        tier2_result: EntityResolutionResult,
        blocking_key: str,
    ) -> EntityResolutionResult:
        """Tier 3: LLM disambiguation for ambiguous candidates.

        ISS-0035: the candidate sentinel may carry a resolution_note
        (suffix_ocr_variant_candidate / person_name_fragment_candidate);
        preserve it so the resolution log records WHY the entity reached
        adjudication.
        """
        if self._instructor_client is None:
            log.warning(
                "entity_resolution.tier3_no_client",
                name=entity.name,
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolution_tier="new",
                similarity_score=tier2_result.similarity_score,
                blocking_key=blocking_key,
                is_new=True,
                candidate_count=tier2_result.candidate_count,
                candidates_json=tier2_result.candidates_json,
                resolution_note=_merge_notes(
                    tier2_result.resolution_note, "llm_disambiguation_failed"
                ),
            )

        # Build properties summary (up to 5 non-empty short properties)
        props_summary = ""
        if entity.properties:
            items = [
                f"{k}: {v}" for k, v in list(entity.properties.items())[:5]
                if v and len(str(v)) < 200
            ]
            if items:
                props_summary = ", ".join(items)

        messages = [
            {
                "role": "system",
                "content": (
                    "You are an entity resolution system. Determine whether "
                    "two entity descriptions refer to the same real-world entity."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Entity A (extracted): {entity.name}, type: {entity.entity_type}"
                    f"{', properties: ' + props_summary if props_summary else ''}\n"
                    f"Entity B (existing): {tier2_result.matched_name}, "
                    f"type: {entity.entity_type}\n\n"
                    f"Are Entity A and Entity B the same real-world entity? "
                    f"Respond YES or NO."
                ),
            },
        ]

        try:
            disambiguation = await self._instructor_client.resolve(
                response_model=DisambiguationResult,
                messages=messages,
            )

            if disambiguation.decision == "YES":
                if tier2_result.resolved_grace_id:
                    await self._persist_alias_if_variant(
                        grace_id=tier2_result.resolved_grace_id,
                        extracted_name=entity.name,
                        matched_name=tier2_result.matched_name or "",
                    )
                log.info(
                    "entity_resolution.tier3_merge",
                    name=entity.name,
                    matched=tier2_result.matched_name,
                    reasoning=disambiguation.reasoning,
                )
                return EntityResolutionResult(
                    extracted_name=entity.name,
                    extracted_type=entity.entity_type,
                    resolved_grace_id=tier2_result.resolved_grace_id,
                    matched_name=tier2_result.matched_name,
                    resolution_tier="llm",
                    similarity_score=tier2_result.similarity_score,
                    blocking_key=blocking_key,
                    is_new=False,
                    candidate_count=tier2_result.candidate_count,
                    candidates_json=tier2_result.candidates_json,
                    llm_reasoning=disambiguation.reasoning,
                    resolution_note=tier2_result.resolution_note,
                )
            else:
                log.info(
                    "entity_resolution.tier3_new",
                    name=entity.name,
                    reasoning=disambiguation.reasoning,
                )
                return EntityResolutionResult(
                    extracted_name=entity.name,
                    extracted_type=entity.entity_type,
                    resolution_tier="new",
                    similarity_score=tier2_result.similarity_score,
                    blocking_key=blocking_key,
                    is_new=True,
                    candidate_count=tier2_result.candidate_count,
                    candidates_json=tier2_result.candidates_json,
                    llm_reasoning=disambiguation.reasoning,
                    resolution_note=tier2_result.resolution_note,
                )

        except ExtractionLLMError as e:
            log.error(
                "entity_resolution.tier3_failed",
                name=entity.name,
                error=str(e),
            )
            return EntityResolutionResult(
                extracted_name=entity.name,
                extracted_type=entity.entity_type,
                resolution_tier="new",
                similarity_score=tier2_result.similarity_score,
                blocking_key=blocking_key,
                is_new=True,
                candidate_count=tier2_result.candidate_count,
                candidates_json=tier2_result.candidates_json,
                resolution_note=_merge_notes(
                    tier2_result.resolution_note, "llm_disambiguation_failed"
                ),
            )

    async def _persist_alias_if_variant(
        self,
        grace_id: str,
        extracted_name: str,
        matched_name: str,
    ) -> None:
        """Persist extracted surface-form as alias when it differs from canonical."""
        if not grace_id:
            return
        if normalize_entity_name(extracted_name, self._strip_suffixes) == normalize_entity_name(
            matched_name, self._strip_suffixes
        ):
            return
        try:
            await append_entity_alias(self._arcade_client, grace_id, extracted_name)
        except Exception:
            log.warning(
                "entity_resolution.alias_persist_failed",
                grace_id=grace_id,
                extracted_name=extracted_name,
                exc_info=True,
            )
