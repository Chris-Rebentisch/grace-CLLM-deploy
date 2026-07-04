"""F-21 regression: _hydrate_result_identities must serialize fact-plane domain
properties (monthly_rent, purchase_price, credit_limit, decision_date, ...) into
the ranked results, while dropping graph bookkeeping and numeric confidence
(D120/D217). Before the fix only INTENT nodes got property prose; fact nodes
rendered as bare "Entity: Type name" and every attribute CQ was unanswerable.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.retrieval_models import RankedResult


def _fake_self(rows):
    client = SimpleNamespace()
    client.execute_cypher = AsyncMock(return_value={"result": rows})
    return SimpleNamespace(client=client)


def _run(fake_self, ranked):
    return asyncio.run(
        RetrievalPipeline._hydrate_result_identities(fake_self, ranked)
    )


def test_fact_domain_properties_merged_confidence_excluded():
    ranked = [
        RankedResult(
            grace_id="lease-1",
            entity_type="Entity",
            name="Entity",
            rerank_score=1.0,
            rrf_score=1.0,
            contributing_strategies=["semantic"],
        )
    ]
    rows = [
        {
            "grace_id": "lease-1",
            "name": "Unit A Lease",
            "labels": ["Lease"],
            "props": {
                "name": "Unit A Lease",
                "monthly_rent": 2350,
                "valid_from": "2026-01-01",
                # bookkeeping / confidence that must NOT surface:
                "grace_id": "lease-1",
                "extraction_confidence": 0.91,
                "_embedding": [0.1, 0.2],
                "sensitivity_tags": "|pii_dense|",
            },
        }
    ]
    out = _run(_fake_self(rows), ranked)
    props = out[0].properties
    assert out[0].entity_type == "Lease"
    assert out[0].name == "Unit A Lease"
    assert props["monthly_rent"] == 2350  # domain fact reaches context (F-21)
    assert props["valid_from"] == "2026-01-01"
    # Bookkeeping + numeric confidence must be excluded.
    assert "extraction_confidence" not in props
    assert "_embedding" not in props
    assert "grace_id" not in props
    assert "sensitivity_tags" not in props


def test_intent_node_still_uses_prose_path_only():
    ranked = [
        RankedResult(
            grace_id="dp-1",
            entity_type="Entity",
            name="Entity",
            rerank_score=1.0,
            rrf_score=1.0,
            contributing_strategies=["semantic"],
        )
    ]
    rows = [
        {
            "grace_id": "dp-1",
            "name": "Prefer entitlement",
            "labels": ["Decision_Principle"],
            "props": {
                "name": "Prefer entitlement",
                "statement": "Pursue entitlement before sale.",
                "monthly_rent": 999,  # not an intent prose key → must NOT leak
            },
        }
    ]
    out = _run(_fake_self(rows), ranked)
    props = out[0].properties
    assert props.get("statement") == "Pursue entitlement before sale."
    # Fact-path merge must not apply to intent nodes.
    assert "monthly_rent" not in props
