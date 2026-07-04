"""Intent-layer write tool — the deterministic write path for human intent/rationale.

NORTH STAR: extract human intent and rationale and connect it to data in a queryable KG.
This module is the *write* half: a thin, deterministic tool the elicitation facilitator
calls AFTER the human confirms the structure. It has zero reasoning latitude — stamps,
the epistemic fence, and edge-dedup are guaranteed here, never improvised by an agent
(D-int-7). Sibling to ``graph_review_writer``; reuses the SAME proven primitives.

R1 import boundary (mirrors ``graph_review_writer`` / ``claim_override_writer``): imports
ONLY ``entity_ops`` / ``relationship_ops`` / ``cypher_utils`` from ``src.graph`` — never
``graph_writer`` (whose ``write_batch`` would break per-fact idempotency).

Guarantees:
  * **Epistemic fence (D-int-3)** — ``epistemic_status`` stamped per type; ``Counterfactual``
    forced ``is_term=False``. Retrieval never serves a rejected alternative as a real term.
  * **Provenance, not decay (D-int-8)** — ``decision_source='human'`` + review provenance +
    ``evidence_origin='human_intent'``; NO D452 decay verdicts (intent is a fixed record).
  * **D106 exact dedup** — ``insert_entity`` canonical-dedups principles on ``name``; a replay
    returns the existing ``grace_id`` (cross-session principle reuse).
  * **Semantic canonicalization (D-int-6)** — ``find_similar_principles`` surfaces near-duplicate
    principles under a different name for the human to confirm reuse (never auto-merge).
  * **Edge-dedup guard (D-int-10)** — ``link_intent`` checks edge existence before insert
    (``insert_relationship`` does not dedup edges). Re-running writes 0 duplicate edges.

Embeddings are passed in (computed by the caller, heat-managed) — this module stays pure,
network-free, and testable. Embed principles over ``statement + applies_when`` (D-int-5).

Design + decision record: ``grace-intent-elicitation/references/intent-layer-design.md``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.graph.entity_models import EntityCreate, RelationshipCreate
from src.graph.entity_ops import insert_entity
from src.graph.relationship_ops import insert_relationship
from src.ontology.intent_models import (
    INTENT_EDGE_TYPES,
    INTENT_MODULE,
    Counterfactual,
    DecisionPrinciple,
    DecisionRationale,
    MandatoryProvision,
)

log = structlog.get_logger()

_VALID_EDGE_TYPES = frozenset(name for name, *_ in INTENT_EDGE_TYPES)
_SAFE_REL_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# Default principle-canonicalization similarity floor — above this, surface as a likely
# duplicate for the human to confirm reuse (D-int-6). Tunable by the harness.
DEFAULT_PRINCIPLE_SIMILARITY = 0.93


def _intent_stamps(*, reviewer: str, session_id: str | None, epistemic_status: str) -> dict:
    """Human-intent provenance bundle (D-int-8). NOT fact-decay — intent is a fixed record.

    Capture-the-why (D452 carve-out): intent nodes deliberately omit ``last_verified_at`` /
    ``verdict`` so the decay batch never ages a captured rationale. Authorization: D-int-8 +
    grace-intent-elicitation design record.
    """
    stamps = {
        "decision_source": "human",
        "reviewed_by": reviewer,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "elicitation_method": "evidence-first+anti-anchoring+laddering",
        "intent_origin": "human_elicitation",
        "epistemic_status": epistemic_status,
    }
    if session_id:
        stamps["review_session_id"] = session_id
    return stamps


def _cosine(a: list[float], b: list[float]) -> float:
    """Pure-python cosine (no numpy dependency at the write layer)."""
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


async def find_similar_principles(
    client: ArcadeClient,
    query_embedding: list[float],
    *,
    top_k: int = 3,
    threshold: float = DEFAULT_PRINCIPLE_SIMILARITY,
) -> list[dict]:
    """Surface existing principles semantically close to a proposed one (D-int-6).

    Returns ``[{name, grace_id, similarity}]`` above ``threshold`` (highest first). The
    harness shows these to the human to confirm reuse — this function NEVER auto-merges.
    Client-side cosine over stored ``_embedding`` (works without the LSM_VECTOR index;
    the index is the server-side optimization for large principle sets).
    """
    rows = (await client.execute_cypher(
        "MATCH (p:Decision_Principle) WHERE p._embedding IS NOT NULL "
        "RETURN p.name AS name, p.grace_id AS grace_id, p._embedding AS e"
    )).get("result", [])
    scored = [
        {"name": r["name"], "grace_id": r["grace_id"], "similarity": round(_cosine(query_embedding, r["e"]), 4)}
        for r in rows if r.get("e")
    ]
    scored = [s for s in scored if s["similarity"] >= threshold]
    scored.sort(key=lambda s: -s["similarity"])
    return scored[:top_k]


async def write_principle(
    client: ArcadeClient,
    principle: DecisionPrinciple,
    *,
    reviewer: str,
    session_id: str | None = None,
    embedding: list[float] | None = None,
) -> dict:
    """Write (or reuse) a reusable Decision_Principle. Exact-dedups on ``name`` (D106).

    Returns ``{grace_id, reused}``. ``reused=True`` means an identically-named principle
    already existed (cross-session reuse) — the embedding is not overwritten.
    """
    entity = EntityCreate(
        entity_type=DecisionPrinciple.GRACE_TYPE,
        ontology_module=INTENT_MODULE,
        human_validated=True,
        evidence_origin="human_intent",
        properties={
            **principle.model_dump(),
            **_intent_stamps(reviewer=reviewer, session_id=session_id,
                             epistemic_status=DecisionPrinciple.EPISTEMIC_STATUS),
        },
    )
    resp = await insert_entity(client, entity, embedding=embedding)
    log.info("intent.principle_written", name=principle.name, grace_id=resp.grace_id,
             reused=resp.canonical_match)
    return {"grace_id": resp.grace_id, "reused": resp.canonical_match}


async def write_rationale(
    client: ArcadeClient,
    rationale: DecisionRationale,
    *,
    reviewer: str,
    session_id: str | None = None,
) -> dict:
    """Write a per-decision Decision_Rationale instance. Returns ``{grace_id, reused}``."""
    entity = EntityCreate(
        entity_type=DecisionRationale.GRACE_TYPE,
        ontology_module=INTENT_MODULE,
        human_validated=True,
        evidence_origin="human_intent",
        properties={
            **rationale.model_dump(),
            **_intent_stamps(reviewer=reviewer, session_id=session_id,
                             epistemic_status=DecisionRationale.EPISTEMIC_STATUS),
        },
    )
    resp = await insert_entity(client, entity)
    log.info("intent.rationale_written", name=rationale.name, grace_id=resp.grace_id)
    return {"grace_id": resp.grace_id, "reused": resp.canonical_match}


async def write_counterfactual(
    client: ArcadeClient,
    counterfactual: Counterfactual,
    *,
    reviewer: str,
    session_id: str | None = None,
) -> dict:
    """Write a FENCED Counterfactual. ``is_term`` is forced False (the fence, D-int-3)."""
    payload = counterfactual.model_dump()
    payload["is_term"] = False  # the fence — never a real term, regardless of input
    entity = EntityCreate(
        entity_type=Counterfactual.GRACE_TYPE,
        ontology_module=INTENT_MODULE,
        human_validated=True,
        evidence_origin="human_intent",
        properties={
            **payload,
            **_intent_stamps(reviewer=reviewer, session_id=session_id,
                             epistemic_status=Counterfactual.EPISTEMIC_STATUS),
        },
    )
    resp = await insert_entity(client, entity)
    log.info("intent.counterfactual_written", name=counterfactual.name, grace_id=resp.grace_id)
    return {"grace_id": resp.grace_id, "reused": resp.canonical_match}


async def write_mandatory_provision(
    client: ArcadeClient,
    provision: MandatoryProvision,
    *,
    reviewer: str,
    session_id: str | None = None,
) -> dict:
    """Write a Mandatory_Provision (v#10, P1) — a fact compelled by statute, not chosen.

    ``epistemic_status='compelled'`` marks it so retrieval/agents never read a
    statute-boilerplate clause as a discretionary decision. No fence (it is real context,
    not a rejected path); no decay (intent layer). Link to the compelled fact with the
    ``compels`` edge via ``link_intent``.
    """
    entity = EntityCreate(
        entity_type=MandatoryProvision.GRACE_TYPE,
        ontology_module=INTENT_MODULE,
        human_validated=True,
        evidence_origin="human_intent",
        properties={
            **provision.model_dump(),
            **_intent_stamps(reviewer=reviewer, session_id=session_id,
                             epistemic_status=MandatoryProvision.EPISTEMIC_STATUS),
        },
    )
    resp = await insert_entity(client, entity)
    log.info("intent.mandatory_provision_written", name=provision.name, grace_id=resp.grace_id,
             source=provision.source_of_compulsion)
    return {"grace_id": resp.grace_id, "reused": resp.canonical_match}


async def _edge_exists(client: ArcadeClient, src: str, edge_type: str, tgt: str) -> bool:
    """Edge-dedup guard (D-int-10) — mirrors graph_review_writer._existing_edge_grace_id."""
    if not _SAFE_REL_TYPE.match(edge_type):
        return False
    s, t = escape_cypher_string(src), escape_cypher_string(tgt)
    res = await client.execute_cypher(
        f"MATCH (a {{grace_id:'{s}'}})-[r:{edge_type}]->(b {{grace_id:'{t}'}}) RETURN count(r) AS n"
    )
    return (res.get("result") or [{"n": 0}])[0].get("n", 0) > 0


async def link_intent(
    client: ArcadeClient,
    *,
    source_grace_id: str,
    edge_type: str,
    target_grace_id: str,
    reviewer: str,
    session_id: str | None = None,
    properties: dict | None = None,
) -> bool:
    """Create an intent edge, dedup-guarded. Returns True if written, False if it existed.

    ``edge_type`` must be one of the five intent edges (validated). Re-running is safe —
    the guard means 0 duplicate edges (proven idempotent).
    """
    if edge_type not in _VALID_EDGE_TYPES:
        raise ValueError(f"edge_type {edge_type!r} not in intent edges {sorted(_VALID_EDGE_TYPES)}")
    if await _edge_exists(client, source_grace_id, edge_type, target_grace_id):
        return False
    await insert_relationship(client, RelationshipCreate(
        relationship_type=edge_type,
        source_grace_id=source_grace_id,
        target_grace_id=target_grace_id,
        ontology_module=INTENT_MODULE,
        properties={**(properties or {}),
                    **_intent_stamps(reviewer=reviewer, session_id=session_id,
                                     epistemic_status="human_rationale")},
    ))
    return True
