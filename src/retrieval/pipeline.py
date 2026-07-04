"""Retrieval pipeline orchestrator — runs strategies, fuses, reranks, serializes."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import Counter

import structlog
from opentelemetry import trace

from src.analytics import metrics as grace_metrics
from src.analytics.llm_instrumentation import grace_call_tags
from src.analytics.pipeline_instrumentation import record_pipeline_stage
from src.graph.arcade_client import ArcadeClient
from src.retrieval.bm25_strategy import BM25SearchIndex
from src.retrieval.document_chunk_strategy import chunk_semantic_search
from src.retrieval.fusion import reciprocal_rank_fusion
from src.retrieval.graph_strategy import graph_search
from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import (
    RankedResult,
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResponse,
)
from src.retrieval.semantic_strategy import SemanticSearchIndex
from src.retrieval.serializer import get_serializer
from src.retrieval.temporal_strategy import (
    apply_temporal_filter,
    temporal_search,
)
from src.retrieval.text_representation import build_text_corpus

logger = structlog.get_logger()
_tracer = trace.get_tracer("grace.retrieval.pipeline")

# --- Relationship-fetch tuning (CF3 onboarding completeness fix) -----------
# Edge properties carried into the serialized context exclude graph
# bookkeeping so only domain-meaningful attributes (e.g. party_role,
# exclusive) reach the LLM. relationship_confidence is dropped per the
# numeric-score discipline (D120/D217). _SYSTEM_VERTEX edges (Query_Event /
# Response_Event / Extraction_Event …) carry no `name` and are filtered by
# the None-name guard in _fetch_relationships.
_EDGE_INTERNAL_PROP_KEYS: frozenset[str] = frozenset(
    {
        "grace_id",
        "_deprecated",
        "_embedding",
        "extraction_event_id",
        "human_validated",
        "extracted_at",
        "updated_at",
        "ontology_module",
        "source_document_id",
        "relationship_confidence",
        # retrieved_from (Query_Event -> entity) plumbing props
        "query_event_id",
        "rank_ordinal",
        "created_at",
    }
)

# G-F5 (D532 (ratified 2026-06-22), Claude-as-LLM test track): intent-layer entity types and the
# *reasoning-prose* property keys that carry their human "why". A "why" query retrieves
# these nodes, but the strategy layer hydrates them with empty properties (semantic/bm25
# store only a text blob), so the captured reasoning never reaches the serialized context
# and a faithful "why" was capped at structure (intent node names + edges only). The keys
# below are all free-text / enum — NO numeric confidence (D120/D217 preserved).
_INTENT_ENTITY_TYPES: frozenset[str] = frozenset(
    {"Decision_Principle", "Decision_Rationale", "Counterfactual", "Mandatory_Provision"}
)
_INTENT_PROSE_KEYS: frozenset[str] = frozenset(
    {
        "statement", "applies_when",                                    # Decision_Principle
        "summary", "constraint", "leverage", "negotiation",             # Decision_Rationale
        "resolver", "stakes",
        "why_rejected", "description", "demanded",                      # Counterfactual
        "basis", "source_of_compulsion",                                # Mandatory_Provision
        "epistemic_status",                                             # polarity signal (all)
    }
)
# F-21 (validation run, 2026-07-01): fact-plane node properties
# (Lease.monthly_rent, Land_Parcel.purchase_price, Loan.credit_limit,
# Zoning_Case.status/decision_date, ...) never reached the serialized context —
# the strategy layer ships empty ``properties`` and the D532 hydration only
# merged reasoning prose for INTENT nodes, so every attribute-kind CQ was
# structurally unanswerable (regeneration abstained faithfully but could not
# answer). The hydration already fetches ``properties(n)``; this denylist lets
# the fact path merge every DOMAIN property while dropping graph bookkeeping and
# — critically — the numeric CONFIDENCE keys (D120/D217; domain numerics like
# rent/price are NOT confidence and are kept). Mirrors ``_EDGE_INTERNAL_PROP_KEYS``.
# Carve-out: pipeline.py is already CF3-allowlisted (D467); this adds a new
# purpose under the same exemption exactly as D532 did (capture-the-why per D356).
# Proposed D-number pending architect ratification (mirror D532).
_NODE_INTERNAL_PROP_KEYS: frozenset[str] = frozenset(
    {
        "grace_id",
        "name",  # already surfaced as the display name
        "_embedding",
        "_deprecated",
        "@rid",
        "@type",
        "record",
        "aliases",
        "extraction_event_id",
        "extracted_at",
        "updated_at",
        "source_document_id",
        "schema_version",
        "human_validated",
        "ontology_module",
        "evidence_origin",
        "extraction_confidence",  # numeric confidence — D120/D217
        "relationship_confidence",  # numeric confidence — D120/D217
        "corroboration_status",
        "corroborating_sender_count",
        "superseded_by",
        "sensitivity_tags",  # governance plumbing, not a domain fact
    }
)

# Safety ceiling on the incident-edge fetch (Cypher LIMIT) and the number of
# edges handed to the token-budgeted serializer after ranking.
_RELATIONSHIP_FETCH_CEILING = 400
_RELATIONSHIP_RENDER_CAP = 120

# F-017 / ISS-0019 (validation run): unnamed Document_Chunk results
# serialized as the useless line `Entity: Entity "Document_Chunk"` — no text
# snippet — while occupying top ranks. Document_Chunk vertices carry no `name`
# property in the graph, so identity hydration skipped them entirely, and the
# frozen serializer (CF3) rendered only the strategy-blob type prefix. The
# constants below drive chunk-aware hydration: a synthesized display name
# (mirroring document_chunk_strategy's "Chunk {idx} of {doc}" convention) plus
# a truncated text snippet, so the chunk's content — the only reason it ranked
# — reaches the serialized context without blowing the token budget.
_CHUNK_ENTITY_TYPE = "Document_Chunk"
_CHUNK_TEXT_KEYS: tuple[str, ...] = ("text", "chunk_text")
_CHUNK_SNIPPET_MAX_CHARS = 200


# F-0028(b) / ISS-0038: superlative phrasings ("largest position by market
# value") were classified to the `vague`/default route instead of structural,
# so aggregate questions bypassed the Cypher-capable structural path. These
# cues are aggregation asks by construction — only a structural (graph/Cypher)
# route can honestly answer them.
_SUPERLATIVE_CUES: frozenset[str] = frozenset(
    {"largest", "smallest", "highest", "lowest", "most", "least", "top", "biggest"}
)

# F-0028(a) / ISS-0038: dotted property reference inside an ORDER BY clause
# (e.g. `ORDER BY p.market_value DESC`). Alias-only ORDER BY targets (e.g.
# `ORDER BY total DESC` after a WITH aggregation) are deliberately NOT
# matched — a NOT-NULL predicate cannot be safely injected for them.
_ORDER_BY_CLAUSE_RE = re.compile(r"\bORDER\s+BY\s+(.+?)(?:\bLIMIT\b|\bSKIP\b|;|$)", re.I | re.S)
_ORDERED_PROP_RE = re.compile(r"\b([A-Za-z_]\w*\.[A-Za-z_]\w*)\b")


def ensure_order_by_not_null(cypher: str) -> str:
    """Post-generation lint for router-generated Cypher: ORDER BY NOT-NULL guard.

    F-0028(a) / ISS-0038 (validation run, CQ-16): ArcadeDB sorts NULLs
    FIRST on `ORDER BY <prop> DESC`, so an aggregate "largest by market_value"
    answer became the one unpriced (NULL) row. Every property ordered by a
    generated query must therefore carry a `<prop> IS NOT NULL` predicate.
    This is a string-level lint applied AFTER Cypher generation (the router's
    structural path) — if the predicate is missing it is appended to the
    existing WHERE clause, or a WHERE clause is inserted before the final
    RETURN when none exists. Queries that already guard, or that order by a
    non-property alias, pass through unchanged.
    """
    m = _ORDER_BY_CLAUSE_RE.search(cypher)
    if not m:
        return cypher

    ordered_props = _ORDERED_PROP_RE.findall(m.group(1))
    missing = [
        prop
        for prop in dict.fromkeys(ordered_props)  # de-dup, keep order
        if not re.search(
            rf"{re.escape(prop)}\s+IS\s+NOT\s+NULL", cypher, re.I
        )
    ]
    if not missing:
        return cypher

    predicate = " AND ".join(f"{prop} IS NOT NULL" for prop in missing)

    # Anchor on the final RETURN (the projection the ORDER BY belongs to).
    return_matches = list(re.finditer(r"\bRETURN\b", cypher, re.I))
    if not return_matches:
        return cypher
    anchor = return_matches[-1]

    # WHERE between the last MATCH (before the final RETURN) and the RETURN
    # itself → extend it with AND; otherwise insert a fresh WHERE clause.
    head = cypher[: anchor.start()]
    match_matches = list(re.finditer(r"\bMATCH\b", head, re.I))
    scan_from = match_matches[-1].end() if match_matches else 0
    has_where = re.search(r"\bWHERE\b", head[scan_from:], re.I) is not None

    if has_where:
        injected = f"AND {predicate} "
    else:
        injected = f"WHERE {predicate} "
    return f"{cypher[: anchor.start()]}{injected}{cypher[anchor.start():]}"


def build_router_schema_prompt(
    entity_types: list[dict],
    relationships: list[dict],
) -> str:
    """Render the schema block for the structural route's text-to-Cypher prompt.

    F-0028(c) / ISS-0038 (validation run, CQ-18): the router's schema
    prompt listed edge NAMES only, so role vocabulary never mapped to edge
    types — "outside counsel" generated keyword-filtered Cypher (0 rows)
    while "advises as legal counsel" found it. Including each relationship's
    DESCRIPTION (and endpoint types) alongside its name gives the generator
    the vocabulary bridge, bounded to the schema text (no corpus content).

    ``entity_types`` items: {"name": str, "properties": [str, ...]?, "description": str?}
    ``relationships`` items: {"name": str, "description": str?, "source": str?, "target": str?}
    """
    lines: list[str] = ["GRAPH SCHEMA", "", "Vertex types:"]
    for et in entity_types:
        line = f"- {et['name']}"
        props = et.get("properties") or []
        if props:
            line += f" (props: {', '.join(props)})"
        if et.get("description"):
            line += f" — {et['description']}"
        lines.append(line)

    lines += ["", "Edge types (direction is source -> target):"]
    for rel in relationships:
        line = f"- {rel['name']}"
        source, target = rel.get("source"), rel.get("target")
        if source and target:
            line += f" ({source} -> {target})"
        # F-0028(c): the description is the load-bearing part — always render
        # it when present so role/synonym vocabulary maps to the edge name.
        if rel.get("description"):
            line += f": {rel['description']}"
        lines.append(line)
    return "\n".join(lines)


def _chunk_snippet(*prop_sources: dict | None) -> str | None:
    """First non-empty chunk text across sources, truncated to ~200 chars.

    F-017 / ISS-0019: the snippet replaces the bare `Entity: Entity
    "Document_Chunk"` line. Newlines are flattened so the snippet stays a
    single serialized-context line (the route-level two-zone scrubber
    ``_scrub_serialized_context`` filters per line by result name/grace_id;
    a multi-line snippet would escape that filter).
    """
    for props in prop_sources:
        if not props:
            continue
        for key in _CHUNK_TEXT_KEYS:
            raw = props.get(key)
            if raw and isinstance(raw, str):
                flat = " ".join(raw.split())
                if len(flat) > _CHUNK_SNIPPET_MAX_CHARS:
                    return flat[:_CHUNK_SNIPPET_MAX_CHARS].rstrip() + "…"
                return flat
    return None


class RetrievalPipeline:
    """Orchestrates the full retrieval flow.

    Manages strategy execution, fusion, reranking, and serialization.
    Tracks per-component latency for analytics.
    """

    def __init__(
        self,
        client: ArcadeClient,
        config: RetrievalConfig,
        semantic_index: SemanticSearchIndex,
        bm25_index: BM25SearchIndex,
        reranker: CrossEncoderReranker,
    ):
        self.client = client
        self.config = config
        self.semantic_index = semantic_index
        self.bm25_index = bm25_index
        self.reranker = reranker
        self._indexes_built = False
        self._mode_latency_history: dict[str, list[float]] = {
            "single_round": [],
            "iterative_round2": [],
        }

    async def build_indexes(self) -> int:
        """Fetch all entities from graph, build semantic + BM25 indexes.

        Called at startup and when entities change.
        Returns entity count.
        """
        t0 = time.monotonic()
        cypher = "MATCH (n) WHERE n._deprecated = false RETURN n"
        result = await self.client.execute_cypher(cypher)
        rows = result.get("result", [])

        entities: list[dict] = []
        for row in rows:
            entity = row.get("n", row) if isinstance(row.get("n"), dict) else row
            entities.append(entity)

        corpus = build_text_corpus(entities)

        # Build both indexes from same corpus
        await self.semantic_index.build_index(corpus)
        self.bm25_index.build_index(corpus)

        self._indexes_built = True
        elapsed = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "pipeline.indexes_built",
            entity_count=len(corpus),
            duration_ms=elapsed,
        )
        return len(corpus)

    async def query(self, query: RetrievalQuery) -> RetrievalResponse:
        """Execute full retrieval pipeline (wrapped with OTel outer span)."""
        with _tracer.start_as_current_span("retrieval.run") as _outer:
            _outer.set_attribute("grace.module", "retrieval")
            _outer.set_attribute("grace.pipeline", "retrieval")
            return await self._run_query(query)

    async def _run_query(self, query: RetrievalQuery) -> RetrievalResponse:
        """Execute full retrieval pipeline.

        1. Run enabled strategies (concurrently where possible)
        2. Apply temporal filter if temporal_as_strategy=False
        3. Fuse via RRF
        4. Rerank top candidates with cross-encoder
        5. Fetch relationships between top results
        6. Serialize context
        7. Return RetrievalResponse with timing
        """
        t_query_start = time.monotonic()
        latency: dict[str, float] = {}
        strategy_results: dict[str, list[RetrievalCandidate]] = {}
        retrieval_mode = "single_round"

        # Auto-build indexes on first query if needed
        if not self._indexes_built:
            t0 = time.monotonic()
            await self.build_indexes()
            latency["index_build"] = round((time.monotonic() - t0) * 1000, 1)

        # 1. Run strategies concurrently
        tasks: dict[str, asyncio.Task] = {}

        if self.config.graph_traversal_enabled:
            tasks["graph"] = asyncio.create_task(
                self._timed_graph(query, latency)
            )

        if self.config.semantic_search_enabled:
            tasks["semantic"] = asyncio.create_task(
                self._timed_semantic(query, latency)
            )

        if self.config.bm25_search_enabled:
            # BM25 is sync — wrap in executor
            tasks["bm25"] = asyncio.create_task(
                self._timed_bm25(query, latency)
            )

        if self.config.temporal_as_strategy:
            tasks["temporal"] = asyncio.create_task(
                self._timed_temporal(query, latency)
            )

        # D467 (Chunk 71 CP4): 5th strategy — chunk-semantic ANN search
        if self.config.chunk_semantic_enabled:
            tasks["chunk_semantic"] = asyncio.create_task(
                self._timed_chunk_semantic(query, latency)
            )

        # Await all strategies
        for name, task in tasks.items():
            strategy_results[name] = await task

        # 2. Apply temporal filter if not running as strategy
        if (
            not self.config.temporal_as_strategy
            and "graph" in strategy_results
            and (query.temporal_start or query.temporal_end)
        ):
            strategy_results["graph"] = apply_temporal_filter(
                strategy_results["graph"],
                query.temporal_start,
                query.temporal_end,
            )

        # 3. Fuse via RRF (round 1)
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="fusion"):
            fused = reciprocal_rank_fusion(strategy_results, k=self.config.rrf_k)
        latency["fusion_round1"] = round((time.monotonic() - t0) * 1000, 1)
        round1_top = fused[: self.config.iterative_round2_seed_limit]

        if self._should_run_iterative_round2(query, round1_top):
            retrieval_mode = "iterative_round2"
            t0 = time.monotonic()
            round2_results = await self._run_round2(query, round1_top, latency)
            async with record_pipeline_stage(pipeline="retrieval", stage="fusion"):
                fused = reciprocal_rank_fusion(
                    self._merge_strategy_results(strategy_results, round2_results),
                    k=self.config.rrf_k,
                )
            latency["fusion_round2"] = round((time.monotonic() - t0) * 1000, 1)

        total_candidates = len(fused)

        # 4. Rerank top candidates
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="rerank"):
            candidates_for_rerank = fused[: self.config.reranker_candidates]
            ranked = self.reranker.rerank(
                query.query_text, candidates_for_rerank, top_k=query.top_k
            )
        latency["rerank"] = round((time.monotonic() - t0) * 1000, 1)

        query_intents = self._classify_query_intents(query.query_text)
        properties_omitted_count = 0
        if self.config.query_aware_filter_enabled:
            ranked, properties_omitted_count = self._filter_ranked_properties(
                ranked, query_intents
            )

        # 4b. F-0026 / ISS-0037 (validation run): build a backfill pool
        # from the next-ranked fused candidates so the chunk-share cap and the
        # post-fusion entity_types filter can replace excluded results instead
        # of shrinking the answer. Pool items skipped the rerank window and
        # the intent property filter — acceptable: their strategy-supplied
        # properties are near-empty and identity hydration (below) merges the
        # canonical domain properties either way.
        selected_ids = {r.grace_id for r in ranked}
        backfill_limit = max(query.top_k * 3, self.config.reranker_candidates)
        backfill_pool = [
            RankedResult(
                grace_id=c.grace_id,
                entity_type=c.entity_type,
                name=c.name,
                properties=c.properties,
                rerank_score=0.0,
                rrf_score=c.rrf_score,
                contributing_strategies=c.contributing_strategies,
            )
            for c in fused
            if c.grace_id not in selected_ids
        ][:backfill_limit]

        # 5. Re-hydrate display identity (name + type) from the graph so the
        # serialized context names the real entity, not its type prefix.
        # F-0026 / ISS-0037: hydration MOVED BEFORE final selection (it
        # previously ran after the relationship fetch) and now covers the
        # backfill pool too, because the semantic/bm25 strategy blobs carry
        # entity_type="Entity" — the post-fusion entity_types filter and the
        # Document_Chunk share cap both need the CANONICAL graph type.
        combined = await self._hydrate_result_identities([*ranked, *backfill_pool])

        # 5b. F-0026 / ISS-0037: post-fusion entity_types filter (closes the
        # chunk-strategy leak) + Document_Chunk share cap with non-chunk
        # backfill. target_len is what the reranker actually returned, so the
        # pool only COMPENSATES for capped/filtered exclusions — it never
        # expands the result list beyond the pre-existing contract.
        ranked = self._select_final_results(
            combined, query, target_len=len(ranked)
        )

        # 5c. F-0042 / ISS-0037 (payload hygiene): strip 768-dim `_embedding`
        # vectors before results reach the serializer or leave the pipeline.
        ranked = self._strip_embedding_vectors(ranked)

        # 5d. Fetch relationships between the FINAL top results.
        t0 = time.monotonic()
        result_ids = [r.grace_id for r in ranked]
        relationships = await self._fetch_relationships(result_ids)
        latency["relationships"] = round((time.monotonic() - t0) * 1000, 1)

        # 6. Serialize context (wrap LLM serializer path in grace_call_tags
        # so nested provider.generate sees grace.module="retrieval",
        # grace.operation="serialize" via ContextVars).
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="serialize"):
            serializer = get_serializer(
                self.config.serialization_format,
                config=self.config,
            )
            if self.config.serialization_format == "llm":
                async with grace_call_tags("retrieval", "serialize"):
                    serialized = await serializer.serialize_async(
                        ranked, relationships, token_budget=self.config.token_budget
                    )
            else:
                serialized = serializer.serialize(
                    ranked, relationships, token_budget=self.config.token_budget
                )
        latency["serialization"] = round((time.monotonic() - t0) * 1000, 1)
        latency["fusion"] = round(
            latency.get("fusion_round1", 0.0) + latency.get("fusion_round2", 0.0), 1
        )
        total_latency_ms = round((time.monotonic() - t_query_start) * 1000, 1)
        latency["total"] = total_latency_ms
        latency_p95_by_mode = self._record_and_compute_p95(retrieval_mode, total_latency_ms)

        # 7. Strategy contributions
        contributions: Counter[str] = Counter()
        for r in ranked:
            for s in r.contributing_strategies:
                contributions[s] += 1

        # Chunk 25 §5.5: zero-result rate counter (scalar).
        if len(ranked) == 0:
            grace_metrics.retrieval_zero_results.add(1)

        return RetrievalResponse(
            query=query.query_text,
            results=ranked,
            serialized_context=serialized,
            serialization_format=self.config.serialization_format,
            total_candidates=total_candidates,
            strategy_contributions=dict(contributions),
            latency_ms=latency,
            retrieval_mode=retrieval_mode,
            query_intents=query_intents,
            properties_omitted_count=properties_omitted_count,
            multi_hop_proxy_score=self._compute_multi_hop_proxy(ranked),
            latency_p95_by_mode_ms=latency_p95_by_mode,
        )

    async def _run_round2(
        self,
        query: RetrievalQuery,
        round1_top: list,
        latency: dict[str, float],
    ) -> dict[str, list[RetrievalCandidate]]:
        """Run selective round-2 retrieval using round-1 seeds."""
        round2_query = query.model_copy(deep=True)
        round2_query.seed_entity_ids = [r.grace_id for r in round1_top]
        round2_query.query_text = self._augment_query_text(query.query_text, round1_top)
        round2_results: dict[str, list[RetrievalCandidate]] = {}
        tasks: dict[str, asyncio.Task] = {}

        if self.config.graph_traversal_enabled:
            tasks["graph"] = asyncio.create_task(
                self._timed_graph_round2(round2_query, latency)
            )
        if self.config.semantic_search_enabled:
            tasks["semantic"] = asyncio.create_task(
                self._timed_semantic_round2(round2_query, latency)
            )

        for name, task in tasks.items():
            round2_results[name] = await task
        return round2_results

    def _should_run_iterative_round2(self, query: RetrievalQuery, round1_top: list) -> bool:
        """Determine whether iterative retrieval should run."""
        mode = (query.iterative_mode or "auto").strip().lower()
        if mode == "on":
            return True
        if mode == "off":
            return False
        if not self.config.iterative_retrieval_enabled:
            return False
        if not self.config.iterative_auto_trigger_enabled:
            return False

        signals = 0
        tokens = query.query_text.split()
        if len(tokens) >= self.config.iterative_min_tokens:
            signals += 1
        cues = {"who", "through", "between", "owns", "manages", "for", "via", "linked"}
        if any(t.lower().strip("?,.") in cues for t in tokens):
            signals += 1
        if len(round1_top) >= 2:
            top_gap = float(round1_top[0].rrf_score) - float(round1_top[1].rrf_score)
            if top_gap < 0.01:
                signals += 1
        return signals >= self.config.iterative_trigger_min_signals

    @staticmethod
    def _merge_strategy_results(
        round1: dict[str, list[RetrievalCandidate]],
        round2: dict[str, list[RetrievalCandidate]],
    ) -> dict[str, list[RetrievalCandidate]]:
        merged: dict[str, list[RetrievalCandidate]] = {}
        keys = set(round1.keys()) | set(round2.keys())
        for key in keys:
            merged[key] = [*(round1.get(key) or []), *(round2.get(key) or [])]
        return merged

    def _augment_query_text(self, query_text: str, round1_top: list) -> str:
        """Expand query with top entity names/types from round-1."""
        hints = []
        for item in round1_top[: self.config.iterative_round2_seed_limit]:
            hints.append(f"{item.name} ({item.entity_type})")
        return query_text if not hints else f"{query_text} related to {'; '.join(hints)}"

    async def _timed_graph_round2(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        t0 = time.monotonic()
        original_depth = self.config.max_hop_depth
        try:
            self.config.max_hop_depth = self.config.iterative_round2_graph_hops
            return await graph_search(self.client, query, self.config)
        finally:
            self.config.max_hop_depth = original_depth
            latency["graph_round2"] = round((time.monotonic() - t0) * 1000, 1)

    async def _timed_semantic_round2(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        t0 = time.monotonic()
        results = await self.semantic_index.search(
            query.query_text, top_k=self.config.semantic_result_limit
        )
        latency["semantic_round2"] = round((time.monotonic() - t0) * 1000, 1)
        return results

    @staticmethod
    def _classify_query_intents(query_text: str) -> list[str]:
        """Rule-based query intent classifier."""
        q = query_text.lower()
        intents: list[str] = []
        # F-017 / ISS-0019 (validation run): CQ-11 "when does the ... lease
        # expire" failed to keep valid_from/valid_to because natural temporal
        # phrasings did not trigger the temporal intent (list had "expires" but
        # not "expire"/"expiry"/"expiration", and lacked renew/due/deadline
        # synonyms). Substring matching is kept (existing behavior); a false
        # positive only widens the property allowlist, which fails open toward
        # answer completeness.
        if any(
            k in q
            for k in (
                "deadline", "date", "when", "quarter", "expires", "valid",
                "expire", "expiry", "expiration", "renew", "renewal",
                "due", "until", "start", "end", "ends",
            )
        ):
            intents.append("temporal")
        # F-017 / ISS-0019: financial phrasings ("what rent is paid", "sold
        # for", "roof bid") likewise never triggered the financial intent.
        if any(
            k in q
            for k in (
                "amount", "premium", "cost", "value", "price",
                "worth", "paid", "sold", "bid", "fee", "rent",
            )
        ):
            intents.append("financial")
        if any(k in q for k in ("owns", "owned", "linked", "relationship", "who", "manages")):
            intents.append("structural")
        # F-0028(b) / ISS-0038 (validation run, A1): superlative
        # phrasings ("largest position by market value") fell through to the
        # default route and were handled as vague/topical, but a superlative
        # is an aggregation ask — only the structural route can answer it.
        # Word-boundary matching so "top" does not fire on "topical" etc.
        if "structural" not in intents and any(
            re.search(rf"\b{cue}\b", q) for cue in _SUPERLATIVE_CUES
        ):
            intents.append("structural")
        if any(k in q for k in ("status", "active", "inactive", "current")):
            intents.append("status")
        if any(k in q for k in ("all", "full", "detail", "comprehensive")):
            intents.append("detail")
        return intents or ["detail"]

    @staticmethod
    def _filter_ranked_properties(results, intents: list[str]) -> tuple[list, int]:
        """Filter ranked properties by intent, preserving identity fields."""
        omitted_count = 0
        filtered = []
        # F-017 / ISS-0019: valid_from/valid_to are part of GrACE's provenance
        # contract — every fact carries temporal validity — so they join the
        # always-keep identity set alongside name/grace_id. They are small,
        # always relevant, and their omission is exactly how a retrieved answer
        # becomes unanswerable (CQ-11 ranked the correct Lease #2 but the
        # serialized context omitted its expiry). Belt-and-braces: they survive
        # even when intent inference misses the temporal cue.
        keep_keys = {"name", "grace_id", "valid_from", "valid_to"}
        temporal_keys = {"valid_from", "valid_to", "effective_date", "expiry_date", "date"}
        financial_keys = {"amount", "premium", "cost", "value", "price"}
        status_keys = {"status", "_deprecated", "human_validated"}
        structural_keys = {"source_document_id", "ontology_module"}

        allow = set(keep_keys)
        if "detail" in intents:
            allow = None
        else:
            if "temporal" in intents:
                allow.update(temporal_keys)
            if "financial" in intents:
                allow.update(financial_keys)
            if "status" in intents:
                allow.update(status_keys)
            if "structural" in intents:
                allow.update(structural_keys)

        for item in results:
            if allow is None:
                filtered.append(item)
                continue
            orig_props = item.properties or {}
            next_props = {}
            for k, v in orig_props.items():
                key_norm = k.lower()
                if k in keep_keys or key_norm in allow:
                    next_props[k] = v
                else:
                    omitted_count += 1
            filtered.append(item.model_copy(update={"properties": next_props}))
        return filtered, omitted_count

    @staticmethod
    def _is_chunk_result(result) -> bool:
        """True when a ranked result is a Document_Chunk.

        F-0026 / ISS-0037: chunk results arrive under TWO identities — the
        chunk_semantic strategy types them ``Document_Chunk``, while the
        semantic/bm25 strategy blobs carry ``entity_type="Entity"`` with the
        type prefix ``"Document_Chunk"`` recovered as the display name
        (``"Document_Chunk: …".split(":")[0]``). Both must count toward the
        chunk-share cap, and both must be excluded by an entity_types filter
        that does not request chunks.
        """
        return (
            result.entity_type == _CHUNK_ENTITY_TYPE
            or result.name == _CHUNK_ENTITY_TYPE
        )

    def _select_final_results(
        self,
        results: list[RankedResult],
        query: RetrievalQuery,
        target_len: int,
    ) -> list[RankedResult]:
        """Post-fusion final selection: entity_types filter + chunk-share cap.

        F-0026 / ISS-0037 (validation run): Document_Chunks are
        triple-fused (semantic + bm25 + chunk_semantic) so RRF structurally
        over-weights them — an intent "why" query at top_k=8 returned 8/8
        chunks (the intent plane started at rank ~14), and 5 golden answers
        were only recoverable at k=20. Two enforcement steps, both on the
        FUSED-AND-RANKED list (so they apply across ALL strategies):

        1. ``entity_types`` filter — when the query restricts types, results
           whose canonical type is not in the list are EXCLUDED (including
           Document_Chunk; the chunk strategy previously ignored the filter
           entirely). Strict by design: unhydrated ``Entity``-typed blobs are
           excluded too, because their real type is unknown.
        2. Chunk-share cap — at most ``ceil(top_k * chunk_share_max)`` of the
           returned results may be Document_Chunk-typed; freed slots are
           backfilled with the next-ranked non-chunk results, and deferred
           chunks re-fill trailing slots so we never return fewer results
           than are available.

        ``results`` is rank-ordered (reranked top-k first, then the fused
        backfill pool in RRF order). ``target_len`` is the size of the
        reranked list before selection (≤ query.top_k) — the pool only
        compensates for exclusions; it never grows the result count.
        """
        if query.entity_types:
            allowed = set(query.entity_types)
            results = [
                r
                for r in results
                if r.entity_type in allowed and not (
                    # A blob-identified chunk (entity_type="Entity",
                    # name="Document_Chunk") must not leak through an
                    # "Entity" filter entry.
                    self._is_chunk_result(r)
                    and _CHUNK_ENTITY_TYPE not in allowed
                )
            ]

        chunk_cap = math.ceil(target_len * self.config.chunk_share_max)

        final: list[RankedResult] = []
        deferred_chunks: list[RankedResult] = []
        chunks_taken = 0
        for r in results:
            if len(final) >= target_len:
                break
            if self._is_chunk_result(r):
                if chunks_taken < chunk_cap:
                    final.append(r)
                    chunks_taken += 1
                else:
                    deferred_chunks.append(r)
            else:
                final.append(r)

        # Never return fewer than available: when there are not enough
        # non-chunk results to fill top_k, over-cap chunks re-enter in rank
        # order.
        for r in deferred_chunks:
            if len(final) >= target_len:
                break
            final.append(r)
        return final

    @staticmethod
    def _strip_embedding_vectors(results: list[RankedResult]) -> list[RankedResult]:
        """Drop 768-dim ``_embedding`` vectors from result properties.

        F-0042 / ISS-0037 (payload hygiene): the graph strategy copies whole
        vertex property maps onto its candidates, and the "detail" intent
        disables the query-aware property filter — so full embedding vectors
        reached reviewer-facing payloads (and the token-budgeted serializer).
        Scrubbed here, at the single exit point of the pipeline.

        F-0047b / ISS-0055 (live-probe follow-up, 2026-07-03): the Layer-1
        provenance bookkeeping (``sensitivity_tag_sources``,
        ``sensitivity_source_total``, ``_privileged_props``) is likewise
        system-plane and must never ship in results — under the
        evidence_scoped posture it would tell a restricted principal the
        NAME of the redacted property (and which tags/sources exist).
        Enforcement reads these via its own authoritative graph fetch,
        never from result payloads, so stripping here is consumer-safe.
        """
        system_plane = ("_embedding", "sensitivity_tag_sources",
                        "sensitivity_source_total", "_privileged_props")
        scrubbed: list[RankedResult] = []
        for r in results:
            if r.properties and any(k in r.properties for k in system_plane):
                props = {k: v for k, v in r.properties.items()
                         if k not in system_plane}
                scrubbed.append(r.model_copy(update={"properties": props}))
            else:
                scrubbed.append(r)
        return scrubbed

    def _record_and_compute_p95(self, mode: str, latency_ms: float) -> dict[str, float]:
        history = self._mode_latency_history.setdefault(mode, [])
        history.append(latency_ms)
        if len(history) > 100:
            del history[0]
        out: dict[str, float] = {}
        for m, values in self._mode_latency_history.items():
            if not values:
                continue
            sorted_vals = sorted(values)
            idx = max(0, math.ceil(0.95 * len(sorted_vals)) - 1)
            out[m] = round(sorted_vals[idx], 1)
        return out

    @staticmethod
    def _compute_multi_hop_proxy(ranked) -> float:
        """Proxy for multi-hop richness based on hop distance and strategy overlap."""
        if not ranked:
            return 0.0
        score = 0.0
        for r in ranked:
            if r.hop_distance and r.hop_distance >= 2:
                score += 1.0
            if len(r.contributing_strategies) >= 2:
                score += 0.5
        return round(score / len(ranked), 3)

    async def _timed_graph(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="graph"):
            results = await graph_search(self.client, query, self.config)
        latency["graph"] = round((time.monotonic() - t0) * 1000, 1)
        return results

    async def _timed_semantic(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="semantic"):
            results = await self.semantic_index.search(
                query.query_text, top_k=self.config.semantic_result_limit
            )
        latency["semantic"] = round((time.monotonic() - t0) * 1000, 1)
        return results

    async def _timed_bm25(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="bm25"):
            results = self.bm25_index.search(
                query.query_text, top_k=self.config.bm25_result_limit
            )
        latency["bm25"] = round((time.monotonic() - t0) * 1000, 1)
        return results

    async def _timed_temporal(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="temporal"):
            results = await temporal_search(self.client, query, self.config)
        latency["temporal"] = round((time.monotonic() - t0) * 1000, 1)
        return results

    async def _timed_chunk_semantic(
        self, query: RetrievalQuery, latency: dict[str, float]
    ) -> list[RetrievalCandidate]:
        """D467 (Chunk 71 CP4): chunk-semantic ANN strategy over Document_Chunk."""
        t0 = time.monotonic()
        async with record_pipeline_stage(pipeline="retrieval", stage="chunk_semantic"):
            results = await chunk_semantic_search(
                self.client,
                query.query_text,
                top_k=self.config.chunk_semantic_top_k,
                ollama_base_url=self.config.ollama_base_url,
            )
        latency["chunk_semantic"] = round((time.monotonic() - t0) * 1000, 1)
        return results

    async def _hydrate_result_identities(self, ranked: list) -> list:
        """Replace each ranked result's display ``name``/``entity_type`` with
        the graph's canonical values, looked up by ``grace_id``.

        CF3 capture-the-why (D356, ratified D530): ``src/retrieval/pipeline.py``
        is a PERMANENT CF3-exempt file (D467), so this does not widen
        ``scripts/check-retrieval-unchanged.sh``. The bm25 strategy (frozen)
        and semantic strategy store only a text blob per entity and
        reconstruct ``name`` by splitting on ``":"`` — which yields the
        *type* prefix (``"Agreement: …".split(":")[0] == "Agreement"``) — and
        hardcode ``entity_type="Entity"``. Correcting that at each strategy
        would require editing the frozen bm25 module; re-hydrating by
        ``grace_id`` here fixes the display for every strategy uniformly and
        idempotently. A miss (id absent from the lookup) leaves the result
        untouched, so the path degrades safely. The hydration query returns
        ``{"result": []}`` under the unit-test mock, making this a no-op
        there.

        G-F5 (D532 (ratified 2026-06-22), Claude-as-LLM test track): the same strategy-blob
        problem leaves intent-layer nodes (Decision_Principle / Decision_Rationale
        / Counterfactual / Mandatory_Provision) with EMPTY ``properties``, so the
        captured human reasoning ("why") never reaches the serialized context and
        a faithful "why" answer was capped at structure (node names + edges). This
        hydration now also pulls ``properties(n)`` and merges the reasoning-PROSE
        subset (``_INTENT_PROSE_KEYS``, all free-text/enum — no numeric confidence,
        D120/D217 preserved) into intent-type results only. Non-intent results keep
        their existing properties untouched (zero blast radius off the intent path).
        This runs AFTER ``_filter_ranked_properties`` so the prose is not re-stripped.
        Carve-out authorization: pipeline.py is already CF3-allowlisted (D467); this
        adds a new *purpose* under D532 (ratified 2026-06-22) (capture-the-why per D356).
        """
        if not ranked:
            return ranked
        ids = [r.grace_id for r in ranked if r.grace_id]
        if not ids:
            return ranked
        id_list = ", ".join(f"'{gid}'" for gid in ids)
        cypher = (
            f"MATCH (n) WHERE n.grace_id IN [{id_list}] "
            f"RETURN n.grace_id AS grace_id, n.name AS name, labels(n) AS labels, "
            f"properties(n) AS props"
        )
        try:
            result = await self.client.execute_cypher(cypher)
            rows = result.get("result", [])
        except Exception:
            logger.warning("pipeline.identity_hydration_failed", exc_info=True)
            return ranked

        identity: dict[str, tuple[str, str | None, dict]] = {}
        for row in rows:
            gid = row.get("grace_id")
            name = row.get("name")
            labels = row.get("labels") or []
            etype = labels[0] if labels else None
            props = row.get("props") or {}
            if gid and name:
                identity[gid] = (name, etype, props)
            elif gid and etype == _CHUNK_ENTITY_TYPE:
                # F-017 / ISS-0019: Document_Chunk vertices carry no `name`,
                # so the `gid and name` guard above skipped them and they kept
                # the strategy-blob identity `Entity "Document_Chunk"`.
                # Synthesize the display name document_chunk_strategy uses so
                # the chunk hydrates like any other result (and so the
                # route-level scrubber can key on a real per-result name).
                chunk_idx = props.get("chunk_index")
                source_doc = props.get("source_document_id")
                if chunk_idx is not None and source_doc:
                    synth_name = f"Chunk {chunk_idx} of {source_doc}"
                else:
                    synth_name = f"Chunk {gid}"
                identity[gid] = (synth_name, etype, props)
        if not identity:
            return ranked

        hydrated = []
        for r in ranked:
            patch = identity.get(r.grace_id)
            if not patch:
                hydrated.append(r)
                continue
            name, etype, props = patch
            update: dict = {"name": name, "entity_type": etype or r.entity_type}
            etype_final = etype or r.entity_type
            # F-017 / ISS-0019: Document_Chunk results render a truncated text
            # snippet instead of the bare `Entity: Entity "Document_Chunk"`
            # line. The graph-side `text` is preferred; the strategy-supplied
            # copy (chunk_semantic) is the fallback. Truncation (~200 chars)
            # keeps the F-21 full-property merge from dumping whole chunk
            # bodies into the token-budgeted context.
            if etype_final == _CHUNK_ENTITY_TYPE:
                snippet = _chunk_snippet(props, r.properties)
                if snippet:
                    update["properties"] = {**(r.properties or {}), "text": snippet}
            # G-F5: inject intent reasoning prose for intent-type nodes only.
            elif etype_final in _INTENT_ENTITY_TYPES:
                prose = {
                    k: v for k, v in props.items()
                    if k in _INTENT_PROSE_KEYS and v is not None and v != ""
                }
                if prose:
                    update["properties"] = {**(r.properties or {}), **prose}
            else:
                # F-21: merge fact-plane domain properties (everything but graph
                # bookkeeping + numeric confidence) so attribute CQs are answerable.
                domain = {
                    k: v for k, v in props.items()
                    if k not in _NODE_INTERNAL_PROP_KEYS and v is not None and v != ""
                }
                if domain:
                    update["properties"] = {**(r.properties or {}), **domain}
            hydrated.append(r.model_copy(update=update))
        return hydrated

    async def _fetch_relationships(self, result_ids: list[str]) -> list[dict]:
        """Fetch edges incident to the top-K result entities, with their
        properties, so an entity's *defining* relationships survive even
        when the far endpoint did not independently rank into the top-K.

        CF3 capture-the-why (D356, ratified D530): ``src/retrieval/pipeline.py``
        is a PERMANENT CF3-exempt file (D467), so this edit does not widen
        ``scripts/check-retrieval-unchanged.sh``; it is the onboarding
        retrieval-completeness fix ratified as D530. The prior query
        required BOTH endpoints in the
        result set (``a IN seeds AND b IN seeds``), which silently dropped
        boundary edges — governing-law Jurisdiction, covered Territory, a
        counterparty whose entity node did not rank. Those thin nodes never
        score on their own, so their edges vanished and search reported a
        fact as absent when it was present. Switching to incident edges
        (``a IN seeds OR b IN seeds``) plus ``properties(r)`` closes that
        gap; the frozen serializer already renders arbitrary edge props —
        it simply received none. Bookkeeping props are stripped here
        (``_EDGE_INTERNAL_PROP_KEYS``) because the serializer is frozen and
        cannot be extended.
        """
        if not result_ids:
            return []

        seed_set = set(result_ids)
        id_list = ", ".join(f"'{gid}'" for gid in result_ids)
        cypher = (
            f"MATCH (a)-[r]->(b) "
            f"WHERE a.grace_id IN [{id_list}] OR b.grace_id IN [{id_list}] "
            f"RETURN a.grace_id AS source_grace_id, a.name AS source_name, "
            f"type(r) AS relationship_type, properties(r) AS rel_properties, "
            f"b.grace_id AS target_grace_id, b.name AS target_name "
            f"LIMIT {_RELATIONSHIP_FETCH_CEILING}"
        )
        try:
            result = await self.client.execute_cypher(cypher)
            rows = result.get("result", [])
        except Exception:
            logger.warning("pipeline.relationships_fetch_failed", exc_info=True)
            return []

        scored: list[tuple[int, dict]] = []
        for row in rows:
            source_name = row.get("source_name")
            target_name = row.get("target_name")
            # Domain entities always carry a `name`; a missing name marks a
            # system vertex (Query_Event / Response_Event / Extraction_Event
            # …). Such edges are graph plumbing, not domain facts — skip.
            if not source_name or not target_name:
                continue
            raw_props = row.get("rel_properties") or {}
            domain_props = {
                k: v
                for k, v in raw_props.items()
                if k not in _EDGE_INTERNAL_PROP_KEYS
            }
            edge = {
                "source_grace_id": row.get("source_grace_id"),
                "source_name": source_name,
                "relationship_type": row.get("relationship_type"),
                "target_grace_id": row.get("target_grace_id"),
                "target_name": target_name,
                **domain_props,
            }
            # Edges internal to the result set (both ends ranked) carry the
            # densest signal and sort first; boundary edges (one end ranked)
            # follow, so token-budget truncation drops the least-connected
            # boundary edges last rather than losing internal structure.
            relevance = (
                int(row.get("source_grace_id") in seed_set)
                + int(row.get("target_grace_id") in seed_set)
            )
            scored.append((relevance, edge))

        scored.sort(key=lambda item: item[0], reverse=True)

        # F-22 / F-10 (validation run, 2026-07-01): the graph carries
        # multiple physical edges for the same (type, source, target) triple
        # (import_extraction did not dedup across documents — the same owns
        # edge ×6), so the serialized context rendered six identical
        # `owns` lines. That both wasted the token budget and CORRUPTED
        # aggregation answers (CQ-15 counted 7 stock positions vs 6). Collapse
        # edges whose rendered line would be byte-identical — same endpoints,
        # type, AND domain properties — keeping the highest-relevance instance
        # (list is already sorted). Edges that differ in any domain property
        # (e.g. distinct party_role) are preserved. Per-edge provenance
        # compression to a footnote is a serializer-side change (CF3-frozen)
        # and is out of scope here.
        deduped: list[dict] = []
        seen: set[tuple] = set()
        for _, edge in scored:
            prop_items = tuple(
                sorted(
                    (k, str(v))
                    for k, v in edge.items()
                    if k
                    not in {
                        "source_grace_id",
                        "source_name",
                        "relationship_type",
                        "target_grace_id",
                        "target_name",
                    }
                )
            )
            key = (
                edge.get("relationship_type"),
                edge.get("source_grace_id"),
                edge.get("target_grace_id"),
                prop_items,
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(edge)
        return deduped[:_RELATIONSHIP_RENDER_CAP]
