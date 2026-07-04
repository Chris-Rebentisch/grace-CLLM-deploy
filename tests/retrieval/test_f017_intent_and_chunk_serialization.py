"""F-017 / ISS-0019 regression (validation run, CQ-11).

Three defect legs, all in ``src/retrieval/pipeline.py`` (CF3-exempt file):

1. "when does the Larkspur Unit 2 lease expire" must infer the *temporal*
   intent so ``valid_from``/``valid_to`` survive the query-aware property
   filter (the answer was ranked #2 but the serialized context omitted it).
2. Belt-and-braces: ``valid_from``/``valid_to`` are part of GrACE's
   provenance contract (every fact carries temporal validity) and must
   survive filtering EVEN when temporal intent is not inferred.
3. Unnamed Document_Chunk results must render a truncated text snippet, not
   the useless bare line ``Entity: Entity "Document_Chunk"``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.retrieval_models import RankedResult
from src.retrieval.serializer import TemplateSerializer


def _ranked(grace_id="lease-1", entity_type="Lease", name="Larkspur Unit 2 Lease",
            properties=None):
    return RankedResult(
        grace_id=grace_id,
        entity_type=entity_type,
        name=name,
        properties=properties or {},
        rerank_score=1.0,
        rrf_score=1.0,
        contributing_strategies=["semantic"],
    )


def _fake_self(rows):
    client = SimpleNamespace()
    client.execute_cypher = AsyncMock(return_value={"result": rows})
    return SimpleNamespace(client=client)


def _hydrate(fake_self, ranked):
    return asyncio.run(
        RetrievalPipeline._hydrate_result_identities(fake_self, ranked)
    )


# --- Leg 1: temporal intent inference on natural phrasings -----------------


def test_cq11_phrasing_infers_temporal_intent():
    intents = RetrievalPipeline._classify_query_intents(
        "when does the Larkspur Unit 2 lease expire"
    )
    assert "temporal" in intents


def test_new_temporal_keywords_trigger_intent():
    for query in (
        "what is the expiry of the policy",
        "expiration of the agreement",
        "is the lease up for renewal",
        "how long until the loan matures",
        "what payment is due next quarter",
    ):
        assert "temporal" in RetrievalPipeline._classify_query_intents(query), query


def test_new_financial_keywords_trigger_intent():
    for query in (
        "how much rent does Trevor Langley pay",
        "what was paid for the parcel",
        "what was the roof bid",
        "how much is the property worth",
    ):
        assert "financial" in RetrievalPipeline._classify_query_intents(query), query


def test_intent_fallback_detail_preserved():
    assert RetrievalPipeline._classify_query_intents("Larkspur") == ["detail"]


def test_temporal_intent_keeps_valid_from_valid_to():
    props = {
        "valid_from": "2024-07-01",
        "valid_to": "2027-06-30",
        "tenant_name": "Trevor Langley",
        "monthly_rent": 1625.0,
    }
    intents = RetrievalPipeline._classify_query_intents(
        "when does the Larkspur Unit 2 lease expire"
    )
    filtered, _ = RetrievalPipeline._filter_ranked_properties(
        [_ranked(properties=props)], intents
    )
    out = filtered[0].properties
    assert out["valid_from"] == "2024-07-01"
    assert out["valid_to"] == "2027-06-30"


# --- Leg 2: valid_from/valid_to always survive (provenance contract) -------


def test_valid_from_valid_to_survive_without_temporal_intent():
    props = {
        "valid_from": "2024-07-01",
        "valid_to": "2027-06-30",
        "tenant_name": "Trevor Langley",
    }
    # Purely financial intent — temporal deliberately NOT inferred.
    filtered, omitted = RetrievalPipeline._filter_ranked_properties(
        [_ranked(properties=props)], ["financial"]
    )
    out = filtered[0].properties
    assert out["valid_from"] == "2024-07-01"
    assert out["valid_to"] == "2027-06-30"
    # Non-allowlisted key still filtered (the filter itself stayed active).
    assert "tenant_name" not in out
    assert omitted == 1


def test_valid_from_valid_to_survive_status_intent():
    filtered, _ = RetrievalPipeline._filter_ranked_properties(
        [_ranked(properties={"valid_to": "2027-06-30", "status": "active"})],
        ["status"],
    )
    assert filtered[0].properties["valid_to"] == "2027-06-30"


# --- Leg 3: Document_Chunk snippet rendering --------------------------------


_LONG_TEXT = (
    "This Lease Agreement is entered into between Larkspur Holdings LLC and "
    "Trevor Langley for Unit 2 of the Larkspur property. The tenant shall pay "
    "monthly rent of $1,625 and the lease term runs from July 1, 2024 through "
    "June 30, 2027, with an option to renew for an additional two-year term "
    "upon written notice no later than ninety days before expiry."
)


def _chunk_rows(text=_LONG_TEXT):
    return [
        {
            "grace_id": "chunk-1",
            "name": None,  # Document_Chunk vertices carry no name
            "labels": ["Document_Chunk"],
            "props": {
                "text": text,
                "chunk_index": 3,
                "source_document_id": "doc-42",
            },
        }
    ]


def test_document_chunk_hydrates_name_and_truncated_snippet():
    ranked = [_ranked(grace_id="chunk-1", entity_type="Entity",
                      name="Document_Chunk", properties={})]
    out = _hydrate(_fake_self(_chunk_rows()), ranked)
    r = out[0]
    assert r.entity_type == "Document_Chunk"
    assert r.name == "Chunk 3 of doc-42"
    snippet = r.properties["text"]
    assert snippet.startswith("This Lease Agreement")
    assert len(snippet) <= 201  # 200 chars + ellipsis
    assert snippet.endswith("…")


def test_document_chunk_short_text_not_truncated():
    ranked = [_ranked(grace_id="chunk-1", entity_type="Entity",
                      name="Document_Chunk", properties={})]
    out = _hydrate(_fake_self(_chunk_rows(text="Short chunk body.")), ranked)
    assert out[0].properties["text"] == "Short chunk body."


def test_document_chunk_serialized_line_is_not_bare_entity_line():
    ranked = [_ranked(grace_id="chunk-1", entity_type="Entity",
                      name="Document_Chunk", properties={})]
    hydrated = _hydrate(_fake_self(_chunk_rows()), ranked)
    context = TemplateSerializer().serialize(hydrated, [], token_budget=2000)
    assert 'Entity: Entity "Document_Chunk"' not in context
    assert 'Entity: Document_Chunk "Chunk 3 of doc-42"' in context
    assert "This Lease Agreement" in context
    # Scrubber contract (_scrub_serialized_context keys on result name per
    # line): the rendered chunk line must contain the result's name.
    chunk_lines = [ln for ln in context.splitlines() if "Chunk 3 of doc-42" in ln]
    assert len(chunk_lines) == 1


def test_document_chunk_snippet_falls_back_to_strategy_text():
    # Graph props lack text (e.g. projection miss) — strategy-supplied copy
    # (chunk_semantic) is the fallback source.
    rows = [
        {
            "grace_id": "chunk-1",
            "name": None,
            "labels": ["Document_Chunk"],
            "props": {"chunk_index": 0, "source_document_id": "doc-7"},
        }
    ]
    ranked = [_ranked(grace_id="chunk-1", entity_type="Entity",
                      name="Document_Chunk",
                      properties={"text": "Strategy-side chunk text."})]
    out = _hydrate(_fake_self(rows), ranked)
    assert out[0].properties["text"] == "Strategy-side chunk text."
    assert out[0].name == "Chunk 0 of doc-7"


def test_document_chunk_snippet_flattens_newlines():
    ranked = [_ranked(grace_id="chunk-1", entity_type="Entity",
                      name="Document_Chunk", properties={})]
    out = _hydrate(_fake_self(_chunk_rows(text="line one\nline two\nline three")),
                   ranked)
    assert out[0].properties["text"] == "line one line two line three"
    assert "\n" not in out[0].properties["text"]


def test_named_entities_unaffected_by_chunk_branch():
    rows = [
        {
            "grace_id": "lease-1",
            "name": "Larkspur Unit 2 Lease",
            "labels": ["Lease"],
            "props": {"name": "Larkspur Unit 2 Lease", "valid_to": "2027-06-30"},
        }
    ]
    out = _hydrate(_fake_self(rows), [_ranked(entity_type="Entity", name="Entity")])
    assert out[0].name == "Larkspur Unit 2 Lease"
    assert out[0].entity_type == "Lease"
    assert out[0].properties["valid_to"] == "2027-06-30"
