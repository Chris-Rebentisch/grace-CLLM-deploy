"""F-22 / F-10 regression: identical edges (same type + endpoints + domain
properties) must collapse to a single serialized line. The graph carries
multiple physical edges per triple (import did not dedup across documents), which
wasted the token budget and corrupted count-style CQs (7 positions vs 6).
Edges that differ in a domain property must be preserved.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.retrieval.pipeline import RetrievalPipeline


def _fake_self(rows):
    client = SimpleNamespace()
    client.execute_cypher = AsyncMock(return_value={"result": rows})
    return SimpleNamespace(client=client)


def _row(src, rel, tgt, props=None):
    return {
        "source_grace_id": src,
        "source_name": src,
        "relationship_type": rel,
        "rel_properties": props or {},
        "target_grace_id": tgt,
        "target_name": tgt,
    }


def test_identical_edges_collapse():
    # owns(LandDev -> Kestrel) x6 identical → one edge.
    rows = [_row("landdev", "owns", "kestrel") for _ in range(6)]
    out = asyncio.run(
        RetrievalPipeline._fetch_relationships(_fake_self(rows), ["landdev"])
    )
    owns = [e for e in out if e["relationship_type"] == "owns"]
    assert len(owns) == 1, f"expected 1 deduped owns edge, got {len(owns)}"


def test_edges_with_distinct_domain_props_preserved():
    rows = [
        _row("p", "holds", "acct", {"party_role": "primary"}),
        _row("p", "holds", "acct", {"party_role": "secondary"}),
    ]
    out = asyncio.run(
        RetrievalPipeline._fetch_relationships(_fake_self(rows), ["p"])
    )
    holds = [e for e in out if e["relationship_type"] == "holds"]
    assert len(holds) == 2, "edges differing in a domain prop must be preserved"
