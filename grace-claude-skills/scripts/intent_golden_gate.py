#!/usr/bin/env python3
"""Golden-gate mock test for the intent layer (both batches). Pass/fail; gates the harness build."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr
add_grace_to_path(); route_logs_to_stderr()
from src.graph.arcade_client import get_arcade_client
from src.shared.embeddings import embed_texts
import numpy as np

results = []
def check(name, ok, detail): results.append((name, ok, detail));

async def q(c, cy): return (await c.execute_cypher(cy))["result"]

async def main():
    c = get_arcade_client()

    # 1) provenance complete on every intent node
    tot=okc=0
    for t in ("Decision_Principle","Decision_Rationale","Counterfactual"):
        r=(await q(c,f"MATCH (n:{t}) RETURN count(n) AS n, "
            "sum(CASE WHEN n.decision_source='human' THEN 1 ELSE 0 END) AS ds, "
            "sum(CASE WHEN n.review_session_id IS NOT NULL THEN 1 ELSE 0 END) AS sid, "
            "sum(CASE WHEN n.epistemic_status IS NOT NULL THEN 1 ELSE 0 END) AS es"))[0]
        tot+=r["n"]; okc+= (r["n"] if (r["ds"]==r["n"] and r["sid"]==r["n"] and r["es"]==r["n"]) else -1)
    check("provenance complete (human + session + epistemic_status on every node)", okc==tot and tot>0, f"{tot} nodes")

    # 2) fence intact
    r=(await q(c,"MATCH (cf:Counterfactual) RETURN count(cf) AS n, "
        "sum(CASE WHEN cf.is_term=false THEN 1 ELSE 0 END) AS fenced, "
        "sum(CASE WHEN cf.epistemic_status='rejected_alternative' THEN 1 ELSE 0 END) AS tagged"))[0]
    check("epistemic fence (every counterfactual is_term=false + tagged)", r["n"]==r["fenced"]==r["tagged"] and r["n"]>0, f"{r['n']} counterfactuals, 0 leaks")

    # 3) fact-retrieval isolation — no counterfactual reachable as a has_obligation target; no node is both
    r=(await q(c,"MATCH ()-[:has_obligation]->(cf:Counterfactual) RETURN count(cf) AS leak"))[0]
    r2=(await q(c,"MATCH (n:Counterfactual) WHERE n:Obligation RETURN count(n) AS dual"))[0]
    check("fact-retrieval isolation (counterfactuals unreachable from fact path)", r["leak"]==0 and r2["dual"]==0, f"{r['leak']} reachable, {r2['dual']} dual-typed")

    # 4) cross-contract principle reuse (>=2 principles span >=2 agreements via explains->obligation)
    rows=await q(c,"MATCH (p:Decision_Principle)-[:explains]->(o:Obligation)<-[:has_obligation]-(a:Agreement) "
        "WITH p, count(DISTINCT a) AS contracts WHERE contracts>=2 RETURN p.name AS name, contracts ORDER BY contracts DESC")
    check("cross-contract principle reuse (>=2 principles span >=2 contracts)", len(rows)>=2,
          "; ".join(f"{x['name']}={x['contracts']}" for x in rows) or "none")

    # 5) embeddings present
    r=(await q(c,"MATCH (p:Decision_Principle) RETURN count(p) AS n, sum(CASE WHEN p._embedding IS NOT NULL THEN 1 ELSE 0 END) AS e"))[0]
    check("embeddings on every principle", r["n"]==r["e"] and r["n"]>0, f"{r['e']}/{r['n']}")

    # 6) no orphan rationale (each has >=1 justifies and >=1 applies_principle)
    rows=await q(c,"MATCH (r:Decision_Rationale) OPTIONAL MATCH (r)-[j:justifies]->() OPTIONAL MATCH (r)-[ap:applies_principle]->() "
        "WITH r, count(DISTINCT j) AS jn, count(DISTINCT ap) AS apn RETURN sum(CASE WHEN jn>0 AND apn>0 THEN 1 ELSE 0 END) AS good, count(r) AS tot")
    r=rows[0]; check("no orphan rationale (each justifies a fact + applies a principle)", r["good"]==r["tot"] and r["tot"]>0, f"{r['good']}/{r['tot']}")

    # 7) traded_for integrity — endpoints are fact-plane (not intent), band present
    rows=await q(c,"MATCH (x)-[r:traded_for]->(y) RETURN count(r) AS n, "
        "sum(CASE WHEN (x:Decision_Principle OR x:Decision_Rationale OR x:Counterfactual OR y:Decision_Principle OR y:Decision_Rationale OR y:Counterfactual) THEN 1 ELSE 0 END) AS bad, "
        "sum(CASE WHEN r.certainty_band IS NOT NULL THEN 1 ELSE 0 END) AS banded")
    r=rows[0]; check("traded_for links two real facts with a certainty band", r["n"]>0 and r["bad"]==0 and r["banded"]==r["n"], f"{r['n']} edges, {r['bad']} bad endpoints")

    # 8) novel-task transfer — a never-seen situation retrieves a sensible principle in top-2
    rows=await q(c,"MATCH (p:Decision_Principle) RETURN p.name AS name, p.statement AS s, p.applies_when AS w")
    NOVEL="A junior partner wants influence over our roadmap but won't commit capital long-term. How do we give them a voice without losing control?"
    texts=[f"{r['s']} Applies when: {r['w']}" for r in rows]
    embs=await embed_texts(texts, base_url="http://localhost:11434")
    qv=np.array((await embed_texts([NOVEL], base_url="http://localhost:11434"))[0])
    cos=lambda a,b:(a@b)/(np.linalg.norm(a)*np.linalg.norm(b))
    ranked=sorted(((float(cos(qv,np.array(e))),r["name"]) for e,r in zip(embs,rows)),key=lambda x:-x[0])
    top2={n for _,n in ranked[:2]}
    expected={"neutralize_a_threat_by_absorbing_it","tie_concession_to_continued_stake","structural_substitute_for_unobtainable_restriction","control_follows_value"}
    check("novel-task transfer (retrieves a relevant principle top-2)", bool(top2 & expected),
          f"top2={list(top2)}")

    await c.aclose()
    print("\n================= GOLDEN GATE =================")
    allok=True
    for name,ok,detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  ({detail})")
        allok &= ok
    print("===============================================")
    print("VERDICT:", "GOLDEN — clear to build the harness" if allok else "NOT GOLDEN — fix before building")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    asyncio.run(main())
