"""Unit tests for graph_review_writer (review-in-place write-back, Option A).

Fully mocked — no ArcadeDB, no Postgres, no Ollama. Verifies the D106 / D452 / D514
contract the module inherits from claim_override_writer:
  * decay stamps + human provenance land on every written fact;
  * enrich = pure insert (no supersession SQL);
  * correct = insert + superseded_by on the OLD id pointing at the NEW id;
  * a correction that canonically matches the original is a no-op, not a self-link.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.extraction import graph_review_writer as grw


@pytest.fixture
def client():
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": []})
    return c


def _entity_resp(grace_id: str, created: bool = True):
    return SimpleNamespace(grace_id=grace_id, created=created)


def _rel_resp(grace_id: str):
    return SimpleNamespace(grace_id=grace_id)


@pytest.mark.asyncio
async def test_enrich_entity_stamps_decay_and_provenance(client, monkeypatch):
    captured = {}

    async def fake_insert(_c, entity):
        captured["entity"] = entity
        return _entity_resp("ge-new", created=True)

    monkeypatch.setattr(grw, "insert_entity", fake_insert)

    out = await grw.enrich_entity(
        client,
        entity_type="Obligation",
        name="Confidentiality obligation",
        properties={"clause": "5.1"},
        reviewer="operator",
        rationale="contract clearly has an NDA clause",
        structural_signal="has_obligation coverage 1/25",
        ontology_module="legal",
        session_id="sess-1",
    )

    assert out == {"grace_id": "ge-new", "created": True}
    props = captured["entity"].properties
    # D452 decay stamps
    assert props["verdict"] == "SUPPORTED"
    assert "last_verified_at" in props
    assert "confidence_at_verification" in props
    # provenance (self-contained in the graph)
    assert props["decision_source"] == "human"
    assert props["reviewed_by"] == "operator"
    assert props["review_rationale"] == "contract clearly has an NDA clause"
    assert props["review_signal"] == "has_obligation coverage 1/25"
    assert props["review_session_id"] == "sess-1"
    # domain props preserved + native human_validated flag
    assert props["clause"] == "5.1"
    assert props["name"] == "Confidentiality obligation"
    assert captured["entity"].human_validated is True
    # enrich never supersedes
    client.execute_cypher.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_relationship_inserts_when_no_existing_edge(client, monkeypatch):
    # dedup check runs first (returns no rows) → insert proceeds
    monkeypatch.setattr(grw, "insert_relationship", AsyncMock(return_value=_rel_resp("re-1")))

    out = await grw.enrich_relationship(
        client,
        relationship_type="has_obligation",
        source_grace_id="agreement-1",
        target_grace_id="ge-new",
        reviewer="operator",
        rationale="links the agreement to its NDA clause",
        structural_signal="has_obligation coverage 1/25",
        ontology_module="legal",
    )

    assert out["created"] is True
    assert out["grace_id"] == "re-1"
    # the only execute_cypher call is the dedup existence check (no supersession)
    client.execute_cypher.assert_awaited_once()


@pytest.mark.asyncio
async def test_enrich_relationship_dedups_existing_edge(monkeypatch):
    # dedup check finds an existing edge → insert is skipped
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": [{"gid": "re-existing"}]})
    insert = AsyncMock(return_value=_rel_resp("re-new"))
    monkeypatch.setattr(grw, "insert_relationship", insert)

    out = await grw.enrich_relationship(
        c,
        relationship_type="has_obligation",
        source_grace_id="agreement-1",
        target_grace_id="ge-new",
        reviewer="operator",
        rationale="re-apply",
        structural_signal="sweep re-run",
    )

    assert out == {"created": False, "deduped": True, "grace_id": "re-existing"}
    insert.assert_not_awaited()


@pytest.mark.asyncio
async def test_enrich_relationship_dedup_off_always_inserts(monkeypatch):
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": [{"gid": "re-existing"}]})
    insert = AsyncMock(return_value=_rel_resp("re-new"))
    monkeypatch.setattr(grw, "insert_relationship", insert)

    out = await grw.enrich_relationship(
        c, relationship_type="has_obligation", source_grace_id="a", target_grace_id="b",
        reviewer="op", rationale="x", structural_signal="y", dedup=False,
    )
    assert out["created"] is True
    insert.assert_awaited_once()
    c.execute_cypher.assert_not_awaited()  # no dedup probe when dedup=False


@pytest.mark.asyncio
async def test_correct_entity_supersedes_old_with_new(client, monkeypatch):
    monkeypatch.setattr(grw, "insert_entity", AsyncMock(return_value=_entity_resp("ge-corrected")))

    out = await grw.correct_entity(
        client,
        old_grace_id="ge-wrong",
        entity_type="Legal_Entity",
        name="Acme Corporation",  # corrected from "Acme Corp"
        reviewer="operator",
        rationale="canonical legal name is Acme Corporation",
        structural_signal="high-stakes party name",
        ontology_module="legal",
    )

    assert out["superseded"] is True
    assert out["new_grace_id"] == "ge-corrected"
    # exactly one supersession UPDATE, OLD pointing at NEW, with valid_to close
    client.execute_cypher.assert_awaited_once()
    query = client.execute_cypher.await_args.args[0]
    assert "ge-wrong" in query
    assert "superseded_by = 'ge-corrected'" in query
    assert "valid_to" in query


@pytest.mark.asyncio
async def test_correct_entity_noop_when_canonical_match(client, monkeypatch):
    # D106 dedup returned the SAME id — the "correction" matched the existing fact.
    monkeypatch.setattr(
        grw, "insert_entity", AsyncMock(return_value=_entity_resp("ge-same", created=False))
    )

    out = await grw.correct_entity(
        client,
        old_grace_id="ge-same",
        entity_type="Legal_Entity",
        name="Acme Corporation",
        reviewer="operator",
        rationale="no change",
        structural_signal="high-stakes party name",
    )

    assert out["superseded"] is False
    # must NOT self-link / orphan the live fact
    client.execute_cypher.assert_not_awaited()


@pytest.mark.asyncio
async def test_correct_relationship_supersedes_with_new_edge_id(client, monkeypatch):
    monkeypatch.setattr(grw, "insert_relationship", AsyncMock(return_value=_rel_resp("re-corrected")))

    out = await grw.correct_relationship(
        client,
        old_grace_id="re-wrong",
        relationship_type="governed_by",
        source_grace_id="agreement-2",
        target_grace_id="jurisdiction-ny",
        reviewer="operator",
        rationale="governing law is New York, not Delaware",
        structural_signal="high-stakes governing law",
    )

    assert out["superseded"] is True
    client.execute_cypher.assert_awaited_once()
    query = client.execute_cypher.await_args.args[0]
    assert "re-wrong" in query
    # supersedes with the NEW edge id, never the source vertex id
    assert "superseded_by = 're-corrected'" in query
