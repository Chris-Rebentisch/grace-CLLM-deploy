"""Unit tests for intent_writer (the intent-layer deterministic write tool).

Fully mocked — no ArcadeDB, no Ollama. Verifies the contract:
  * provenance stamps land, fact-decay does NOT (D-int-8);
  * the Counterfactual fence is forced (is_term=False, rejected_alternative) (D-int-3);
  * edge-type validation + edge-dedup guard (D-int-10);
  * semantic principle canonicalization surfaces near-duplicates (D-int-6).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.extraction import intent_writer as iw
from src.ontology.intent_models import (
    Counterfactual, DecisionPrinciple, DecisionRationale, MandatoryProvision,
)


@pytest.fixture
def client():
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": []})
    return c


def _entity_resp(grace_id: str, canonical_match: bool = False):
    return SimpleNamespace(grace_id=grace_id, canonical_match=canonical_match, created=not canonical_match)


@pytest.mark.asyncio
async def test_write_principle_provenance_not_decay(client, monkeypatch):
    captured = {}

    async def fake_insert(_c, entity, embedding=None):
        captured["entity"] = entity
        captured["embedding"] = embedding
        return _entity_resp("p-1")

    monkeypatch.setattr(iw, "insert_entity", fake_insert)
    p = DecisionPrinciple(name="risk_follows_control",
                          statement="Risk sits with the party that controls the decisions.",
                          applies_when="Allocating loss between parties with asymmetric control.")
    out = await iw.write_principle(client, p, reviewer="human:test", session_id="s-1",
                                   embedding=[0.1] * 768)
    props = captured["entity"].properties
    assert out == {"grace_id": "p-1", "reused": False}
    assert captured["entity"].entity_type == "Decision_Principle"
    assert captured["entity"].evidence_origin == "human_intent"
    # provenance present
    assert props["decision_source"] == "human"
    assert props["epistemic_status"] == "human_rationale"
    assert props["review_session_id"] == "s-1"
    assert props["intent_origin"] == "human_elicitation"
    # fact-decay ABSENT (intent is a fixed record, not a degrading fact)
    assert "last_verified_at" not in props
    assert "verdict" not in props
    assert captured["embedding"] == [0.1] * 768


@pytest.mark.asyncio
async def test_write_principle_reused_flag(client, monkeypatch):
    monkeypatch.setattr(iw, "insert_entity",
                        AsyncMock(return_value=_entity_resp("p-existing", canonical_match=True)))
    p = DecisionPrinciple(name="control_follows_value", statement="...", applies_when="...")
    out = await iw.write_principle(client, p, reviewer="r")
    assert out == {"grace_id": "p-existing", "reused": True}


@pytest.mark.asyncio
async def test_counterfactual_fence_forced(client, monkeypatch):
    captured = {}

    async def fake_insert(_c, entity, embedding=None):
        captured["entity"] = entity
        return _entity_resp("cf-1")

    monkeypatch.setattr(iw, "insert_entity", fake_insert)
    # adversarial input: is_term=True should be FORCED to False by the writer
    cf = Counterfactual(name="rejected_x", description="d", demanded="dm", why_rejected="w", is_term=True)
    await iw.write_counterfactual(client, cf, reviewer="r")
    props = captured["entity"].properties
    assert props["is_term"] is False
    assert props["epistemic_status"] == "rejected_alternative"


@pytest.mark.asyncio
async def test_write_mandatory_provision_compelled_not_decision(client, monkeypatch):
    captured = {}

    async def fake_insert(_c, entity, embedding=None):
        captured["entity"] = entity
        return _entity_resp("mp-1")

    monkeypatch.setattr(iw, "insert_entity", fake_insert)
    # P1: a statute-compelled clause — epistemic_status='compelled', NOT human_rationale
    mp = MandatoryProvision(name="aks_fmv", source_of_compulsion="statute",
                            basis="Anti-Kickback safe harbor requires FMV in any such contract.")
    out = await iw.write_mandatory_provision(client, mp, reviewer="r", session_id="s")
    props = captured["entity"].properties
    assert out == {"grace_id": "mp-1", "reused": False}
    assert props["epistemic_status"] == "compelled"          # not a chosen why
    assert props["source_of_compulsion"] == "statute"
    assert props["decision_source"] == "human"
    assert "last_verified_at" not in props                   # intent layer: no decay
    # no is_term fence — it is real context, not a rejected path
    assert "is_term" not in props


@pytest.mark.asyncio
async def test_rationale_salience_fields_flow_through(client, monkeypatch):
    captured = {}

    async def fake_insert(_c, entity, embedding=None):
        captured["entity"] = entity
        return _entity_resp("r-1")

    monkeypatch.setattr(iw, "insert_entity", fake_insert)
    # P3: controlling vs subordinate reasons
    r = DecisionRationale(name="x", summary="s", controlling_reason="regulatory ownership",
                          subordinate_reasons="indemnity (would not have flipped it)")
    await iw.write_rationale(client, r, reviewer="r")
    props = captured["entity"].properties
    assert props["controlling_reason"] == "regulatory ownership"
    assert "would not have flipped" in props["subordinate_reasons"]


@pytest.mark.asyncio
async def test_link_intent_accepts_new_edges(monkeypatch):
    # P2 depends_on + P1 compels (v#10) + P4 specializes (v#11) are valid intent edges
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": [{"n": 0}]})
    monkeypatch.setattr(iw, "insert_relationship", AsyncMock())
    for et in ("depends_on", "compels", "specializes"):
        assert await iw.link_intent(c, source_grace_id="a", edge_type=et,
                                    target_grace_id="b", reviewer="r") is True


@pytest.mark.asyncio
async def test_link_intent_rejects_invalid_edge_type(client):
    with pytest.raises(ValueError):
        await iw.link_intent(client, source_grace_id="a", edge_type="has_obligation",
                             target_grace_id="b", reviewer="r")


@pytest.mark.asyncio
async def test_link_intent_dedup_guard_skips_existing(monkeypatch):
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": [{"n": 1}]})  # edge exists
    insert = AsyncMock()
    monkeypatch.setattr(iw, "insert_relationship", insert)
    wrote = await iw.link_intent(c, source_grace_id="a", edge_type="explains",
                                 target_grace_id="b", reviewer="r")
    assert wrote is False
    insert.assert_not_called()


@pytest.mark.asyncio
async def test_link_intent_inserts_when_absent(monkeypatch):
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": [{"n": 0}]})  # no edge
    insert = AsyncMock()
    monkeypatch.setattr(iw, "insert_relationship", insert)
    wrote = await iw.link_intent(c, source_grace_id="a", edge_type="traded_for",
                                 target_grace_id="b", reviewer="r")
    assert wrote is True
    insert.assert_called_once()


@pytest.mark.asyncio
async def test_find_similar_principles_threshold_and_order():
    c = SimpleNamespace()
    c.execute_cypher = AsyncMock(return_value={"result": [
        {"name": "near", "grace_id": "g-near", "e": [1.0, 0.0, 0.0]},   # cosine 1.0
        {"name": "far", "grace_id": "g-far", "e": [0.0, 1.0, 0.0]},     # cosine 0.0
    ]})
    hits = await iw.find_similar_principles(c, [1.0, 0.0, 0.0], threshold=0.93)
    assert [h["name"] for h in hits] == ["near"]           # 'far' filtered out
    assert hits[0]["similarity"] == 1.0
