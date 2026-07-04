"""Tests for the ISS-0037 / ISS-0038 retrieval-quality fixes (all deps mocked).

F-0026 / ISS-0037 — Document_Chunk crowding cap + post-fusion entity_types
filter (chunk-strategy leak closed).
F-0042 / ISS-0037 — `_embedding` vectors stripped from result properties.
F-0028 / ISS-0038 — Cypher-router quality: ORDER BY NOT-NULL lint,
superlative → structural classification, relationship descriptions in the
router schema prompt.

Pure unit tests: no Postgres, no ArcadeDB, no Ollama — strategies, graph
client, and reranker are mocked (DB-safety rail).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.graph.arcade_client import ArcadeClient, ArcadeConfig
from src.retrieval.bm25_strategy import BM25SearchIndex
from src.retrieval.pipeline import (
    RetrievalPipeline,
    build_router_schema_prompt,
    ensure_order_by_not_null,
)
from src.retrieval.reranker import CrossEncoderReranker
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import (
    RankedResult,
    RetrievalCandidate,
    RetrievalQuery,
)
from src.retrieval.semantic_strategy import SemanticSearchIndex


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pipeline(config: RetrievalConfig | None = None) -> RetrievalPipeline:
    cfg = config or RetrievalConfig()
    client = ArcadeClient(config=ArcadeConfig())
    client.execute_cypher = AsyncMock(return_value={"result": []})
    semantic_index = MagicMock(spec=SemanticSearchIndex)
    semantic_index.search = AsyncMock(return_value=[])
    bm25_index = MagicMock(spec=BM25SearchIndex)
    bm25_index.search = MagicMock(return_value=[])
    reranker = MagicMock(spec=CrossEncoderReranker)
    p = RetrievalPipeline(
        client=client,
        config=cfg,
        semantic_index=semantic_index,
        bm25_index=bm25_index,
        reranker=reranker,
    )
    p._indexes_built = True
    return p


def _ranked(
    gid: str,
    entity_type: str = "Legal_Entity",
    name: str | None = None,
    properties: dict | None = None,
) -> RankedResult:
    return RankedResult(
        grace_id=gid,
        entity_type=entity_type,
        name=name if name is not None else f"entity-{gid}",
        properties=properties or {},
        rerank_score=0.5,
        rrf_score=0.02,
        contributing_strategies=["semantic"],
    )


def _chunk(gid: str) -> RankedResult:
    """A canonical chunk_semantic-typed Document_Chunk result."""
    return _ranked(gid, entity_type="Document_Chunk", name=f"Chunk 1 of doc-{gid}")


def _blob_chunk(gid: str) -> RankedResult:
    """A semantic/bm25 blob chunk: entity_type='Entity', name='Document_Chunk'."""
    return _ranked(gid, entity_type="Entity", name="Document_Chunk")


# ---------------------------------------------------------------------------
# F-0026 / ISS-0037 (a): Document_Chunk share cap + backfill
# ---------------------------------------------------------------------------

class TestChunkShareCap:
    def test_cap_enforced_and_entities_backfilled(self):
        """8/8-chunk head with trailing entities → cap=4 chunks, 4 entities."""
        p = _pipeline()  # chunk_share_max default 0.5
        query = RetrievalQuery(query_text="why", top_k=8)
        chunks = [_chunk(f"c{i}") for i in range(8)]
        entities = [_ranked(f"e{i}", entity_type="Decision_Rationale") for i in range(6)]
        final = p._select_final_results([*chunks, *entities], query, target_len=8)

        assert len(final) == 8
        chunk_count = sum(1 for r in final if p._is_chunk_result(r))
        assert chunk_count == 4  # ceil(8 * 0.5)
        # backfilled entities are the next-ranked non-chunks, in order
        assert [r.grace_id for r in final if not p._is_chunk_result(r)] == [
            "e0", "e1", "e2", "e3",
        ]
        # capped chunks are the top-ranked ones, order preserved
        assert [r.grace_id for r in final if p._is_chunk_result(r)] == [
            "c0", "c1", "c2", "c3",
        ]

    def test_blob_identified_chunks_count_toward_cap(self):
        """Semantic/bm25 blob chunks (type='Entity', name='Document_Chunk')
        count toward the cap exactly like chunk_semantic-typed ones."""
        p = _pipeline()
        query = RetrievalQuery(query_text="why", top_k=4)
        results = [
            _blob_chunk("c0"),
            _chunk("c1"),
            _blob_chunk("c2"),  # over cap (ceil(4*0.5)=2) → deferred
            _ranked("e0"),
            _ranked("e1"),
        ]
        final = p._select_final_results(results, query, target_len=4)
        assert [r.grace_id for r in final] == ["c0", "c1", "e0", "e1"]

    def test_all_chunks_never_returns_fewer_than_available(self):
        """With no non-chunk results available, deferred chunks re-fill —
        the cap never shrinks the response below what is available."""
        p = _pipeline()
        query = RetrievalQuery(query_text="why", top_k=6)
        chunks = [_chunk(f"c{i}") for i in range(6)]
        final = p._select_final_results(chunks, query, target_len=6)
        assert len(final) == 6
        assert [r.grace_id for r in final] == [f"c{i}" for i in range(6)]

    def test_no_chunks_is_a_noop(self):
        p = _pipeline()
        query = RetrievalQuery(query_text="who owns what", top_k=5)
        entities = [_ranked(f"e{i}") for i in range(5)]
        final = p._select_final_results(list(entities), query, target_len=5)
        assert final == entities

    def test_cap_disabled_at_share_1(self):
        p = _pipeline(RetrievalConfig(chunk_share_max=1.0))
        query = RetrievalQuery(query_text="why", top_k=4)
        results = [_chunk(f"c{i}") for i in range(4)] + [_ranked("e0")]
        final = p._select_final_results(results, query, target_len=4)
        assert [r.grace_id for r in final] == ["c0", "c1", "c2", "c3"]

    @pytest.mark.asyncio
    async def test_end_to_end_backfill_pulls_from_fused_pool(self):
        """Full _run_query path: reranker returns chunk-heavy top-k; the
        fused pool holds trailing entities → cap enforced, pool backfills."""
        p = _pipeline(
            RetrievalConfig(
                graph_traversal_enabled=False,
                bm25_search_enabled=False,
                chunk_semantic_enabled=False,
            )
        )
        # Semantic strategy returns 4 chunks then 2 entities (rank order).
        sem = [
            RetrievalCandidate(
                grace_id=f"c{i}",
                entity_type="Document_Chunk",
                name=f"Chunk {i} of doc",
                score=0.9 - i * 0.01,
                strategy="semantic",
                rank=i + 1,
            )
            for i in range(4)
        ] + [
            RetrievalCandidate(
                grace_id=f"e{i}",
                entity_type="Decision_Rationale",
                name=f"rationale-{i}",
                score=0.5 - i * 0.01,
                strategy="semantic",
                rank=5 + i,
            )
            for i in range(2)
        ]
        p.semantic_index.search = AsyncMock(return_value=sem)
        # Reranker echoes the chunk-heavy top-4 (crowding scenario).
        p.reranker.rerank.return_value = [
            _ranked(f"c{i}", entity_type="Document_Chunk", name=f"Chunk {i} of doc")
            for i in range(4)
        ]
        resp = await p.query(RetrievalQuery(query_text="why was it acquired", top_k=4))
        ids = [r.grace_id for r in resp.results]
        assert len(ids) == 4
        # ceil(4*0.5)=2 chunks max; e0/e1 backfilled from the fused pool.
        assert ids == ["c0", "c1", "e0", "e1"]


# ---------------------------------------------------------------------------
# F-0026 / ISS-0037 (b): post-fusion entity_types filter
# ---------------------------------------------------------------------------

class TestEntityTypesPostFusionFilter:
    def test_chunks_do_not_leak_past_entity_types(self):
        p = _pipeline()
        query = RetrievalQuery(
            query_text="why",
            top_k=5,
            entity_types=["Decision_Rationale", "Counterfactual"],
        )
        results = [
            _chunk("c0"),
            _blob_chunk("c1"),
            _ranked("r0", entity_type="Decision_Rationale"),
            _ranked("x0", entity_type="Legal_Entity"),
            _ranked("k0", entity_type="Counterfactual"),
        ]
        final = p._select_final_results(results, query, target_len=5)
        assert [r.grace_id for r in final] == ["r0", "k0"]
        assert all(not p._is_chunk_result(r) for r in final)

    def test_blob_chunk_does_not_leak_through_entity_filter_entry(self):
        """A blob chunk is typed 'Entity'; even a filter that (oddly) requests
        'Entity' must not admit something that is really a Document_Chunk."""
        p = _pipeline()
        query = RetrievalQuery(query_text="q", top_k=3, entity_types=["Entity"])
        results = [_blob_chunk("c0"), _ranked("e0", entity_type="Entity")]
        final = p._select_final_results(results, query, target_len=3)
        assert [r.grace_id for r in final] == ["e0"]

    def test_chunks_admitted_when_explicitly_requested(self):
        p = _pipeline(RetrievalConfig(chunk_share_max=1.0))
        query = RetrievalQuery(
            query_text="q", top_k=3, entity_types=["Document_Chunk"]
        )
        results = [_chunk("c0"), _ranked("e0")]
        final = p._select_final_results(results, query, target_len=3)
        assert [r.grace_id for r in final] == ["c0"]

    @pytest.mark.asyncio
    async def test_end_to_end_entity_types_excludes_chunks(self):
        p = _pipeline(
            RetrievalConfig(
                graph_traversal_enabled=False,
                bm25_search_enabled=False,
                chunk_semantic_enabled=False,
            )
        )
        sem = [
            RetrievalCandidate(
                grace_id="c0",
                entity_type="Document_Chunk",
                name="Chunk 0 of doc",
                score=0.99,
                strategy="semantic",
                rank=1,
            ),
            RetrievalCandidate(
                grace_id="r0",
                entity_type="Decision_Rationale",
                name="rationale-0",
                score=0.4,
                strategy="semantic",
                rank=2,
            ),
        ]
        p.semantic_index.search = AsyncMock(return_value=sem)
        p.reranker.rerank.return_value = [
            _ranked("c0", entity_type="Document_Chunk", name="Chunk 0 of doc"),
            _ranked("r0", entity_type="Decision_Rationale"),
        ]
        resp = await p.query(
            RetrievalQuery(
                query_text="why", top_k=2, entity_types=["Decision_Rationale"]
            )
        )
        assert [r.grace_id for r in resp.results] == ["r0"]
        assert all(r.entity_type == "Decision_Rationale" for r in resp.results)


# ---------------------------------------------------------------------------
# F-0042 / ISS-0037: _embedding payload hygiene
# ---------------------------------------------------------------------------

class TestEmbeddingScrub:
    def test_strip_embedding_vectors_unit(self):
        results = [
            _ranked("e0", properties={"_embedding": [0.1] * 768, "status": "active"}),
            _ranked("e1", properties={"status": "active"}),
        ]
        out = RetrievalPipeline._strip_embedding_vectors(results)
        assert "_embedding" not in out[0].properties
        assert out[0].properties["status"] == "active"
        assert out[1] is results[1]  # untouched objects pass through

    def test_strip_sensitivity_provenance_bookkeeping(self):
        """F-0047b / ISS-0055 (live-probe follow-up): the Layer-1 provenance
        props are system-plane — shipping them would tell a restricted
        principal the NAME of a scrubbed property. Stripped at the same
        pipeline exit as _embedding."""
        results = [
            _ranked("e0", properties={
                "sensitivity_tag_sources": '{"privileged": {"ids": ["email:x"]}}',
                "sensitivity_source_total": 2,
                "_privileged_props": '["secret_amount"]',
                "public_note": "kept",
            }),
        ]
        out = RetrievalPipeline._strip_embedding_vectors(results)
        assert set(out[0].properties) == {"public_note"}

    @pytest.mark.asyncio
    async def test_no_embedding_leaves_pipeline(self):
        """A graph-strategy-shaped result carrying a full vertex property map
        (incl. _embedding) is scrubbed before it leaves the pipeline."""
        p = _pipeline(
            RetrievalConfig(
                graph_traversal_enabled=False,
                bm25_search_enabled=False,
                chunk_semantic_enabled=False,
            )
        )
        p.semantic_index.search = AsyncMock(
            return_value=[
                RetrievalCandidate(
                    grace_id="e0",
                    entity_type="Legal_Entity",
                    name="Acme",
                    properties={"_embedding": [0.1] * 768, "name": "Acme"},
                    score=0.9,
                    strategy="semantic",
                    rank=1,
                )
            ]
        )
        p.reranker.rerank.return_value = [
            _ranked(
                "e0",
                properties={"_embedding": [0.1] * 768, "name": "Acme"},
            )
        ]
        # "all details" → detail intent → property filter passes everything,
        # which is exactly the F-0042 leak path.
        resp = await p.query(RetrievalQuery(query_text="all details on Acme", top_k=1))
        assert resp.results
        for r in resp.results:
            assert "_embedding" not in (r.properties or {})
        assert "_embedding" not in resp.serialized_context


# ---------------------------------------------------------------------------
# F-0028(a) / ISS-0038: ORDER BY NOT-NULL lint
# ---------------------------------------------------------------------------

class TestOrderByNotNullLint:
    def test_inserts_where_when_absent(self):
        q = "MATCH (p:Position) RETURN p.name, p.market_value ORDER BY p.market_value DESC"
        out = ensure_order_by_not_null(q)
        assert "WHERE p.market_value IS NOT NULL" in out
        # predicate lands before RETURN, i.e. inside the MATCH clause
        assert out.index("IS NOT NULL") < out.index("RETURN")

    def test_appends_and_to_existing_where(self):
        q = (
            "MATCH (p:Position) WHERE p.asset_class = 'equity' "
            "RETURN p.name ORDER BY p.market_value DESC"
        )
        out = ensure_order_by_not_null(q)
        assert "AND p.market_value IS NOT NULL" in out
        assert out.count("WHERE") == 1

    def test_noop_when_predicate_already_present(self):
        q = (
            "MATCH (p:Position) WHERE p.market_value IS NOT NULL "
            "RETURN p.name ORDER BY p.market_value DESC"
        )
        assert ensure_order_by_not_null(q) == q

    def test_noop_without_order_by(self):
        q = "MATCH (p:Position) RETURN p.name"
        assert ensure_order_by_not_null(q) == q

    def test_noop_for_alias_only_order_by(self):
        """ORDER BY on a WITH/aggregation alias has no property to guard."""
        q = (
            "MATCH (p:Position) WITH count(p) AS total "
            "RETURN total ORDER BY total DESC"
        )
        assert ensure_order_by_not_null(q) == q

    def test_multiple_ordered_properties_all_guarded(self):
        q = (
            "MATCH (p:Position) RETURN p.name "
            "ORDER BY p.market_value DESC, p.acquired_at ASC"
        )
        out = ensure_order_by_not_null(q)
        assert "p.market_value IS NOT NULL" in out
        assert "p.acquired_at IS NOT NULL" in out

    def test_limit_suffix_preserved(self):
        q = "MATCH (p:Position) RETURN p.name ORDER BY p.market_value DESC LIMIT 1"
        out = ensure_order_by_not_null(q)
        assert "WHERE p.market_value IS NOT NULL" in out
        assert out.rstrip().endswith("LIMIT 1")


# ---------------------------------------------------------------------------
# F-0028(b) / ISS-0038: superlative → structural classification
# ---------------------------------------------------------------------------

class TestSuperlativeClassification:
    @pytest.mark.parametrize(
        "query",
        [
            "largest position by market value",
            "smallest holding in the portfolio",
            "highest premium policy",
            "lowest rent lease",
            "biggest tract acquired",
            "top counterparty by exposure",
            "entity with the most agreements",
            "least valuable parcel",
        ],
    )
    def test_superlatives_classify_structural(self, query):
        assert "structural" in RetrievalPipeline._classify_query_intents(query)

    def test_no_duplicate_structural_tag(self):
        intents = RetrievalPipeline._classify_query_intents(
            "who owns the largest position"
        )
        assert intents.count("structural") == 1

    def test_word_boundary_no_false_positive(self):
        """'top' must not fire inside 'topical'; plain queries stay non-structural."""
        intents = RetrievalPipeline._classify_query_intents(
            "topical overview of the insurance policies"
        )
        assert "structural" not in intents


# ---------------------------------------------------------------------------
# F-0028(c) / ISS-0038: router schema prompt carries relationship descriptions
# ---------------------------------------------------------------------------

class TestRouterSchemaPrompt:
    def test_prompt_includes_relationship_names_and_descriptions(self):
        prompt = build_router_schema_prompt(
            entity_types=[
                {
                    "name": "Law_Firm",
                    "properties": ["name", "jurisdiction"],
                    "description": "An outside legal services provider",
                },
                {"name": "Legal_Entity", "properties": ["name"]},
            ],
            relationships=[
                {
                    "name": "advises",
                    "source": "Law_Firm",
                    "target": "Legal_Entity",
                    "description": (
                        "Provides legal counsel to; includes outside counsel "
                        "engagements"
                    ),
                },
                {"name": "owns", "source": "Legal_Entity", "target": "Legal_Entity"},
            ],
        )
        # edge name AND its role-vocabulary description are both present
        assert "advises" in prompt
        assert "outside counsel" in prompt
        assert "Law_Firm -> Legal_Entity" in prompt
        # vertex section
        assert "Law_Firm" in prompt
        assert "jurisdiction" in prompt
        # description-less entries still render their name
        assert "- owns" in prompt

    def test_prompt_handles_missing_optional_fields(self):
        prompt = build_router_schema_prompt(
            entity_types=[{"name": "Person"}],
            relationships=[{"name": "works_for"}],
        )
        assert "- Person" in prompt
        assert "- works_for" in prompt
