#!/usr/bin/env python3
"""Frontier text-to-Cypher pilot — 50 queries against the live grace ArcadeDB.

The Cypher for each query was written by Claude (this session) reading ONLY the
schema (vertex/edge inventory + key property names). No expected-result peeking
during generation: all 50 Cyphers were committed to this file before any
execution. Verdict assigned per-row after running.

  cd ~/grace && .venv/bin/python ~/grace-claude-skills/scripts/cypher_pilot.py
"""
from __future__ import annotations
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

QUERIES: list[dict] = [
    # === S1 Structural 1-hop (12) — the main failure class ===
    {"id": "S1-01", "q": "Which agreements are governed by Delaware law?",
     "cy": "MATCH (a:Agreement)-[:governed_by]->(j:Jurisdiction) WHERE j.name='Delaware' RETURN a.name AS agreement"},
    {"id": "S1-02", "q": "What law governs the Accuray distributor agreement?",
     "cy": "MATCH (a:Agreement)-[:governed_by]->(j:Jurisdiction) WHERE a.name CONTAINS 'Accuray' OR a.name CONTAINS 'LINAC' RETURN a.name AS agreement, j.name AS jurisdiction"},
    {"id": "S1-03", "q": "What territory does the Airspan Networks distributor agreement cover?",
     "cy": "MATCH (a:Agreement)-[:covers_territory]->(t:Territory) WHERE a.name CONTAINS 'Airspan' RETURN a.name AS agreement, t.name AS territory"},
    {"id": "S1-04", "q": "What product does the Anixa OntoChem collaboration agreement concern?",
     "cy": "MATCH (a:Agreement)-[:concerns_subject]->(p:Product) WHERE a.name CONTAINS 'Anixa' RETURN a.name AS agreement, p.name AS product"},
    {"id": "S1-05", "q": "Who are the parties to the Tom Watson Adams Golf endorsement agreement?",
     "cy": "MATCH (e:Legal_Entity)-[:party_to]->(a:Agreement) WHERE a.name CONTAINS 'Watson' OR a.name CONTAINS 'Adams Golf' RETURN a.name AS agreement, e.name AS party"},
    {"id": "S1-06", "q": "Which agreements does Xencor participate in (as a party)?",
     "cy": "MATCH (e:Legal_Entity)-[:party_to]->(a:Agreement) WHERE e.name CONTAINS 'Xencor' RETURN e.name AS entity, a.name AS agreement"},
    {"id": "S1-07", "q": "List all Jurisdiction names in the graph.",
     "cy": "MATCH (j:Jurisdiction) RETURN j.name AS jurisdiction ORDER BY jurisdiction"},
    {"id": "S1-08", "q": "What Legal Entities are formed in Delaware?",
     "cy": "MATCH (e:Legal_Entity) WHERE e.jurisdiction_of_formation CONTAINS 'Delaware' RETURN e.name AS entity, e.jurisdiction_of_formation AS formed_in"},
    {"id": "S1-09", "q": "Which agreements grant a license to which IP asset?",
     "cy": "MATCH (a:Agreement)-[:grants_license]->(ip:IP_Asset) RETURN a.name AS agreement, ip.name AS ip_asset"},
    {"id": "S1-10", "q": "What payment terms exist for the Xencor Aimmune agreement?",
     "cy": "MATCH (a:Agreement)-[:has_payment_term]->(pt:Payment_Term) WHERE a.name CONTAINS 'Xencor' RETURN a.name AS agreement, pt.name AS payment_term, pt.payment_type AS payment_type, pt.amount_or_rate AS amount"},
    {"id": "S1-11", "q": "Which agreements have a milestone defined?",
     "cy": "MATCH (a:Agreement)-[:has_milestone]->(m:Milestone) RETURN a.name AS agreement, m.name AS milestone"},
    {"id": "S1-12", "q": "List all agreements with an effective_date.",
     "cy": "MATCH (a:Agreement) WHERE a.effective_date IS NOT NULL RETURN a.name AS agreement, a.effective_date AS effective_date ORDER BY effective_date"},

    # === S2 Entity-scoped specific clause (8) ===
    {"id": "S2-13", "q": "What are the indemnification obligations in the Xencor Aimmune agreement?",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE a.name CONTAINS 'Xencor' AND (o.obligation_type='indemnification' OR o.summary CONTAINS 'indemnif') RETURN o.summary AS obligation, o.obligation_type AS type"},
    {"id": "S2-14", "q": "What are the termination obligations of the Apollo Establishment Labs agreement?",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE a.name CONTAINS 'Apollo' AND (o.obligation_type='termination' OR o.summary CONTAINS 'terminat') RETURN o.summary AS obligation, o.obligation_type AS type"},
    {"id": "S2-15", "q": "What are the confidentiality obligations of the Anixa OntoChem agreement?",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE a.name CONTAINS 'Anixa' AND (o.obligation_type='confidentiality' OR o.summary CONTAINS 'confidential' OR o.summary CONTAINS 'trade secret') RETURN o.summary AS obligation, o.obligation_type AS type"},
    {"id": "S2-16", "q": "What are all obligations owed to Tom Watson?",
     "cy": "MATCH (o:Obligation)-[:owed_to]->(e:Legal_Entity) WHERE e.name CONTAINS 'Watson' RETURN o.summary AS obligation, e.name AS owed_to"},
    {"id": "S2-17", "q": "What are all obligations whose obligor is Aimmune?",
     "cy": "MATCH (o:Obligation)-[:obligation_of]->(e:Legal_Entity) WHERE e.name CONTAINS 'Aimmune' RETURN o.summary AS obligation, e.name AS obligor"},
    {"id": "S2-18", "q": "Who is the obligor and obligee for each obligation in the Tom Watson agreement?",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE a.name CONTAINS 'Watson' OPTIONAL MATCH (o)-[:obligation_of]->(obr:Legal_Entity) OPTIONAL MATCH (o)-[:owed_to]->(obe:Legal_Entity) RETURN o.summary AS obligation, obr.name AS obligor, obe.name AS obligee"},
    {"id": "S2-19", "q": "Which obligations mention 'exclusive license'?",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE o.summary CONTAINS 'exclusive' AND o.summary CONTAINS 'license' RETURN a.name AS agreement, o.summary AS obligation"},
    {"id": "S2-20", "q": "What is the payment_type of each Payment_Term linked to Xencor Aimmune?",
     "cy": "MATCH (a:Agreement)-[:has_payment_term]->(pt:Payment_Term) WHERE a.name CONTAINS 'Xencor' RETURN pt.name AS payment_term, pt.payment_type AS payment_type"},

    # === M1 Multi-hop (8) ===
    {"id": "M1-21", "q": "Which agreements share at least one party with the Xencor Aimmune agreement?",
     "cy": "MATCH (xa:Agreement)<-[:party_to]-(e:Legal_Entity)-[:party_to]->(other:Agreement) WHERE xa.name CONTAINS 'Xencor' AND other.grace_id <> xa.grace_id RETURN DISTINCT other.name AS shares_party_with_xencor, e.name AS shared_party"},
    {"id": "M1-22", "q": "For each Jurisdiction, how many agreements does it govern?",
     "cy": "MATCH (j:Jurisdiction)<-[:governed_by]-(a:Agreement) RETURN j.name AS jurisdiction, count(a) AS n_agreements ORDER BY n_agreements DESC"},
    {"id": "M1-23", "q": "Which Legal_Entities are party to the most agreements (top 5)?",
     "cy": "MATCH (e:Legal_Entity)-[:party_to]->(a:Agreement) RETURN e.name AS entity, count(a) AS n_agreements ORDER BY n_agreements DESC LIMIT 5"},
    {"id": "M1-24", "q": "What principles explain obligations in the Adaptimmune MD Anderson agreement?",
     "cy": "MATCH (p:Decision_Principle)-[:explains]->(o:Obligation)<-[:has_obligation]-(a:Agreement) WHERE a.name CONTAINS 'Adaptimmune' OR a.name CONTAINS 'Strategic Collaboration' RETURN DISTINCT p.name AS principle, p.statement AS statement"},
    {"id": "M1-25", "q": "Which counterfactuals reject alternatives to obligations in the Xencor Aimmune agreement?",
     "cy": "MATCH (cf:Counterfactual)-[:rejected_alternative_to]->(o:Obligation)<-[:has_obligation]-(a:Agreement) WHERE a.name CONTAINS 'Xencor' RETURN cf.name AS counterfactual, o.summary AS rejected_against"},
    {"id": "M1-26", "q": "Find pairs of agreements that share a governing Jurisdiction.",
     "cy": "MATCH (a:Agreement)-[:governed_by]->(j:Jurisdiction)<-[:governed_by]-(b:Agreement) WHERE a.grace_id < b.grace_id RETURN a.name AS agreement_a, b.name AS agreement_b, j.name AS shared_jurisdiction ORDER BY shared_jurisdiction"},
    {"id": "M1-27", "q": "Which entities both grant and receive licenses?",
     "cy": "MATCH (e:Legal_Entity)-[:party_to]->(a1:Agreement)-[:grants_license]->(:IP_Asset) MATCH (e)-[:party_to]->(a2:Agreement)-[:licensed_to]->(e) RETURN DISTINCT e.name AS entity"},
    {"id": "M1-28", "q": "For each principle, count the number of facts it explains.",
     "cy": "MATCH (p:Decision_Principle)-[:explains]->(o) RETURN p.name AS principle, count(o) AS facts_explained ORDER BY facts_explained DESC"},

    # === I1 Intent-anchored (6) ===
    {"id": "I1-29", "q": "What rationale justifies the obligation about Aimmune bearing regulatory costs?",
     "cy": "MATCH (r:Decision_Rationale)-[:justifies]->(o:Obligation) WHERE o.summary CONTAINS 'Aimmune' AND o.summary CONTAINS 'regulatory cost' RETURN r.name AS rationale, o.summary AS justifies"},
    {"id": "I1-30", "q": "What principle does the rationale 'adaptimmune_mda_protocol_control' apply?",
     "cy": "MATCH (r:Decision_Rationale {name:'adaptimmune_mda_protocol_control'})-[:applies_principle]->(p:Decision_Principle) RETURN p.name AS principle, p.statement AS statement"},
    {"id": "I1-31", "q": "What was the rejected alternative to MD Anderson protocol amendments?",
     "cy": "MATCH (cf:Counterfactual)-[:rejected_alternative_to]->(o:Obligation) WHERE o.summary CONTAINS 'Protocol' AND o.summary CONTAINS 'amendments' RETURN cf.name AS counterfactual, cf.description AS description, cf.why_rejected AS why_rejected, o.summary AS rejected_against"},
    {"id": "I1-32", "q": "Find all counterfactuals and the facts they reject against.",
     "cy": "MATCH (cf:Counterfactual)-[:rejected_alternative_to]->(o) RETURN cf.name AS counterfactual, o.summary AS rejected_against, labels(o)[0] AS target_type"},
    {"id": "I1-33", "q": "What parent principle does 'regulatory_owner_controls_the_artifact' specialize?",
     "cy": "MATCH (child:Decision_Principle {name:'regulatory_owner_controls_the_artifact'})-[:specializes]->(parent:Decision_Principle) RETURN parent.name AS parent_principle, parent.statement AS parent_statement"},
    {"id": "I1-34", "q": "Which mandatory provisions compel which obligations?",
     "cy": "MATCH (mp:Mandatory_Provision)-[:compels]->(o:Obligation) RETURN mp.name AS mandatory_provision, o.summary AS compels"},

    # === A1 Aggregation (8) ===
    {"id": "A1-35", "q": "Total number of obligations.",
     "cy": "MATCH (o:Obligation) RETURN count(o) AS n_obligations"},
    {"id": "A1-36", "q": "Number of obligations grouped by obligation_type, descending.",
     "cy": "MATCH (o:Obligation) RETURN o.obligation_type AS obligation_type, count(*) AS n ORDER BY n DESC"},
    {"id": "A1-37", "q": "Number of parties per agreement (top 5).",
     "cy": "MATCH (e:Legal_Entity)-[:party_to]->(a:Agreement) RETURN a.name AS agreement, count(e) AS n_parties ORDER BY n_parties DESC LIMIT 5"},
    {"id": "A1-38", "q": "Average obligation count per agreement.",
     "cy": "MATCH (a:Agreement) OPTIONAL MATCH (a)-[:has_obligation]->(o:Obligation) WITH a, count(o) AS n RETURN avg(n) AS avg_obligations_per_agreement"},
    {"id": "A1-39", "q": "How many agreements are in the graph?",
     "cy": "MATCH (a:Agreement) RETURN count(a) AS n_agreements"},
    {"id": "A1-40", "q": "Count of Decision_Principles.",
     "cy": "MATCH (p:Decision_Principle) RETURN count(p) AS n_principles"},
    {"id": "A1-41", "q": "Most common Jurisdiction by agreement count.",
     "cy": "MATCH (j:Jurisdiction)<-[:governed_by]-(a:Agreement) RETURN j.name AS jurisdiction, count(a) AS n ORDER BY n DESC LIMIT 1"},
    {"id": "A1-42", "q": "Count of Counterfactuals grouped by epistemic_status.",
     "cy": "MATCH (cf:Counterfactual) RETURN cf.epistemic_status AS epistemic_status, count(*) AS n"},

    # === H1 Hard / negation / composition (4) ===
    {"id": "H1-43", "q": "Which agreements have NO governing law assigned?",
     "cy": "MATCH (a:Agreement) WHERE NOT (a)-[:governed_by]->() RETURN a.name AS agreement_without_jurisdiction"},
    {"id": "H1-44", "q": "Which Legal_Entities are party to more than 3 agreements?",
     "cy": "MATCH (e:Legal_Entity)-[:party_to]->(a:Agreement) WITH e, count(a) AS n WHERE n > 3 RETURN e.name AS entity, n ORDER BY n DESC"},
    {"id": "H1-45", "q": "Find Jurisdictions that govern no agreement.",
     "cy": "MATCH (j:Jurisdiction) WHERE NOT (j)<-[:governed_by]-() RETURN j.name AS unused_jurisdiction"},
    {"id": "H1-46", "q": "Which obligations have no obligor recorded?",
     "cy": "MATCH (o:Obligation) WHERE NOT (o)-[:obligation_of]->() RETURN count(o) AS obligations_without_obligor"},

    # === M2 Mixed sanity (4) ===
    {"id": "M2-47", "q": "Show the Tom Watson agreement and all its obligations.",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE a.name CONTAINS 'Watson' RETURN a.name AS agreement, o.summary AS obligation, o.obligation_type AS type"},
    {"id": "M2-48", "q": "Find all obligations of type 'payment' with their agreement names.",
     "cy": "MATCH (a:Agreement)-[:has_obligation]->(o:Obligation) WHERE o.obligation_type='payment' RETURN a.name AS agreement, o.summary AS obligation LIMIT 50"},
    {"id": "M2-49", "q": "List Decision_Principles with their applies_when scope.",
     "cy": "MATCH (p:Decision_Principle) RETURN p.name AS principle, p.statement AS statement, p.applies_when AS applies_when"},
    {"id": "M2-50", "q": "Find Agreement governed_by Jurisdiction triples (all of them).",
     "cy": "MATCH (a:Agreement)-[:governed_by]->(j:Jurisdiction) RETURN a.name AS agreement, j.name AS jurisdiction ORDER BY jurisdiction, agreement"},
]


async def main():
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    c = get_arcade_client()
    results = []
    for qi in QUERIES:
        out = {"id": qi["id"], "q": qi["q"], "cy": qi["cy"]}
        try:
            r = await c.execute_cypher(qi["cy"])
            rows = r.get("result", []) or []
            out["row_count"] = len(rows)
            out["sample"] = rows[:5]
            out["error"] = None
        except Exception as e:
            out["row_count"] = None
            out["sample"] = None
            out["error"] = str(e)[:300]
        results.append(out)
    await c.aclose()
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    route_logs_to_stderr()
    asyncio.run(main())
