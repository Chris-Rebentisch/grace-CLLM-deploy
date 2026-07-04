#!/usr/bin/env python3
"""Retrieval golden gate — domain-agnostic pass/fail invariants over the live
retrieval API + graph. CF3-safe: a CLIENT validator; never touches src/retrieval/*.

It encodes the contract that must hold for a *healthy, current* retrieval surface,
with anchors discovered from the graph at runtime (no domain hardcoding). The two
staleness traps that this session hit are first-class gates (GATE-2 index, GATE-3
code) because both are invisible in a raw 200 response.

  GATE-1 services_up        API /config 200 + ArcadeDB reachable
  GATE-2 grounding          every returned grace_id resolves in the graph (stale INDEX guard)
  GATE-3 hydration          grounded results carry real names + types, not bare prefixes (stale CODE guard)
  GATE-4 strategy_plurality >=2 strategies contribute on a normal query
  GATE-5 heat_clean         no gpt-oss / llama* loaded after a query (only nomic allowed)
  GATE-6 intent_reachable   a why-shaped query surfaces an intent-plane node (skips if no intent layer)
  GATE-7 fence_label        a surfaced Counterfactual keeps its type label (structural fence travels; skips if none)
  GATE-8 path_b_edges       a seeded query renders >=1 boundary edge with a real endpoint name (D530)

Plus an informational AUDIT block for the documented known-gaps (numeric leak R-F5,
structural recall R-F2) — reported, not gated.

Exit 0 iff all non-skipped gates PASS. Heat: only nomic-embed-text. No generation model.

  python3 retrieval_golden_gate.py
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

API = "http://127.0.0.1:8000"
_INTENT_TYPES = {"Decision_Principle", "Decision_Rationale", "Counterfactual", "Mandatory_Provision"}
_SYSTEM_TYPES = {"Query_Event", "Response_Event", "Extraction_Event", "Correction_Event",
                 "Migration_Event", "GovernanceDecision_Event"}

_results: list[tuple[str, str, str]] = []  # (gate, status PASS/FAIL/SKIP, detail)


def record(gate: str, ok: bool | None, detail: str) -> None:
    status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
    _results.append((gate, status, detail))
    mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "–"}[status]
    print(f"  {mark} {gate:<22} {status:<4} {detail}")


async def _query(cx, payload: dict) -> dict:
    r = await cx.post(f"{API}/api/retrieval/query", json=payload,
                      headers={"X-Graph-Scope": "all"})
    r.raise_for_status()
    return r.json()


async def _resolve(client, gids: list[str]) -> dict[str, str]:
    if not gids:
        return {}
    id_list = ", ".join(f"'{g}'" for g in gids)
    rows = (await client.execute_cypher(
        f"MATCH (n) WHERE n.grace_id IN [{id_list}] "
        "RETURN n.grace_id AS gid, labels(n)[0] AS t"))["result"]
    return {r["gid"]: r["t"] for r in rows}


async def _run() -> int:
    add_grace_to_path()
    import httpx
    from src.graph.arcade_client import get_arcade_client

    client = get_arcade_client()
    print("RETRIEVAL GOLDEN GATE\n")

    async with httpx.AsyncClient(timeout=120.0) as cx:
        # GATE-1 services_up
        try:
            cfg = await cx.get(f"{API}/api/retrieval/config")
            arcade_ok = bool((await client.execute_cypher("MATCH (n) RETURN count(n) AS c"))["result"])
            record("GATE-1 services_up", cfg.status_code == 200 and arcade_ok,
                   f"config={cfg.status_code} arcade={'up' if arcade_ok else 'down'}")
        except Exception as e:
            record("GATE-1 services_up", False, f"error: {e}")
            await client.aclose()
            return _summary()

        # Discover a domain anchor: a sample non-system, non-intent node name.
        anchor = (await client.execute_cypher(
            "MATCH (n) WHERE n.name IS NOT NULL AND NOT n:Query_Event AND NOT n:Response_Event "
            "RETURN n.name AS name, labels(n)[0] AS t LIMIT 25"))["result"]
        domain_anchor = next((a for a in anchor
                              if a["t"] not in _SYSTEM_TYPES | _INTENT_TYPES), None)
        anchor_query = (" ".join((domain_anchor["name"] if domain_anchor else "agreement terms")
                                 .split()[:6]))

        # GATE-2 grounding + GATE-3 hydration + GATE-4 plurality (one query)
        try:
            resp = await _query(cx, {"query_text": anchor_query, "top_k": 8})
            res = resp.get("results", [])
            gids = [r["grace_id"] for r in res]
            truth = await _resolve(client, gids)
            phantom = [g for g in gids if g not in truth]
            record("GATE-2 grounding", len(phantom) == 0,
                   f"{len(gids)-len(phantom)}/{len(gids)} resolve"
                   + (f"  PHANTOM={len(phantom)} (rebuild index!)" if phantom else ""))

            # hydration: grounded results should not be bare type-prefixes/Entity
            bad = []
            for r in res:
                if r["grace_id"] not in truth:
                    continue
                real_type = truth[r["grace_id"]]
                if r.get("entity_type") == "Entity" or r.get("name") == real_type:
                    bad.append(r["grace_id"][:8])
            record("GATE-3 hydration", len(bad) == 0,
                   "names+types hydrated" if not bad else
                   f"{len(bad)} bare-prefix results (stale CODE?): {bad[:3]}")

            contribs = resp.get("strategy_contributions", {})
            record("GATE-4 strategy_plurality", len(contribs) >= 2,
                   f"strategies={contribs}")
        except Exception as e:
            record("GATE-2 grounding", False, f"error: {e}")
            record("GATE-3 hydration", False, "skipped (query failed)")
            record("GATE-4 strategy_plurality", False, "skipped (query failed)")

        # GATE-5 heat_clean — no generation model loaded
        try:
            ps = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=15).stdout.lower()
            hot = [m for m in ("gpt-oss", "llama3.3", "llama3", "llama2", "mixtral", "qwen2.5:7b") if m in ps]
            record("GATE-5 heat_clean", len(hot) == 0,
                   "only embeddings" if not hot else f"FORBIDDEN MODEL HOT: {hot}")
        except Exception as e:
            record("GATE-5 heat_clean", None, f"ollama ps unavailable: {e}")

        # GATE-6 intent_reachable — why-query surfaces an intent-plane node
        princ = (await client.execute_cypher(
            "MATCH (p:Decision_Principle) RETURN p.statement AS s LIMIT 1"))["result"]
        if not princ:
            record("GATE-6 intent_reachable", None, "no intent layer in graph")
        else:
            why = "Why " + " ".join((princ[0]["s"] or "decision rationale").split()[:8])
            iresp = await _query(cx, {"query_text": why, "top_k": 6})
            itypes = await _resolve(client, [r["grace_id"] for r in iresp.get("results", [])])
            hit = [t for t in itypes.values() if t in _INTENT_TYPES]
            record("GATE-6 intent_reachable", len(hit) > 0,
                   f"intent nodes in top-6: {hit or 'NONE'}")

        # GATE-7 fence_label — a surfaced Counterfactual keeps its type
        cf = (await client.execute_cypher(
            "MATCH (c:Counterfactual) RETURN c.name AS n LIMIT 1"))["result"]
        if not cf:
            record("GATE-7 fence_label", None, "no counterfactuals in graph")
        else:
            cfq = " ".join((cf[0]["n"] or "").replace("_", " ").split()[:8])
            cresp = await _query(cx, {"query_text": cfq, "top_k": 8})
            ctypes = await _resolve(client, [r["grace_id"] for r in cresp.get("results", [])])
            # find any returned counterfactual; assert API entity_type label says Counterfactual
            cf_ids = [r for r in cresp.get("results", []) if ctypes.get(r["grace_id"]) == "Counterfactual"]
            if not cf_ids:
                record("GATE-7 fence_label", None, "no counterfactual surfaced for probe query")
            else:
                labelled = all(r.get("entity_type") == "Counterfactual" for r in cf_ids)
                record("GATE-7 fence_label", labelled,
                       f"{len(cf_ids)} CF surfaced; type label intact={labelled}")

        # GATE-8 path_b_edges — seeded query renders a boundary edge w/ real endpoint
        seed = (await client.execute_cypher(
            "MATCH (a)-[r]->(b) WHERE a.name IS NOT NULL AND b.name IS NOT NULL "
            "AND NOT a:Query_Event RETURN a.grace_id AS gid LIMIT 1"))["result"]
        if not seed:
            record("GATE-8 path_b_edges", None, "no seedable connected node")
        else:
            sresp = await _query(cx, {"query_text": anchor_query,
                                      "seed_entity_ids": [seed[0]["gid"]],
                                      "iterative_mode": "on", "top_k": 8})
            ctx = sresp.get("serialized_context", "")
            has_edge = "-->" in ctx or "--[" in ctx
            record("GATE-8 path_b_edges", has_edge,
                   "boundary edges rendered" if has_edge else "no edges in serialized_context")

            # ---- informational AUDIT (documented known gaps; not gated) ----
            print("\nAUDIT (documented gaps — informational, not gated):")
            leak = "confidence_at_verification=" in ctx or "relationship_confidence=" in ctx
            print(f"  {'⚠' if leak else '·'} R-F5 numeric-leak in serialized_context: "
                  f"{'PRESENT (D120/D217)' if leak else 'clean'}")

        # GATE-9 structural_recall — the Claude-wrapped router fix. Runs the
        # structural battery's Claude-generated Cyphers through the deterministic
        # path and asserts retrieval-axis recall >= threshold. This is the gate
        # that proves the 1/6 -> ~100% lift holds (extraction-gap items excluded).
        import cypher_exec
        battery_path = (Path(__file__).resolve().parent.parent
                        / "runs" / "retrieval-probe" / "structural_battery.json")
        if not battery_path.exists():
            record("GATE-9 structural_recall", None, "no structural_battery.json")
        else:
            spec = json.loads(battery_path.read_text())
            schema = await cypher_exec.introspect_schema(client)
            retr = [q for q in spec["queries"] if q["axis"] == "retrieval"]
            found = 0
            for it in retr:
                out = await cypher_exec.validate_and_run(client, it["cypher"], schema)
                blob = json.dumps(out["rows"], default=str).lower()
                if out["ok"] and (it["expect"] == "ANY" and out["row_count"]
                                  or it["expect"].lower() in blob):
                    found += 1
            recall = found / len(retr) if retr else 0.0
            record("GATE-9 structural_recall", recall >= 0.9,
                   f"router structural recall {found}/{len(retr)} = {recall*100:.0f}% "
                   f"(semantic-only baseline was ~1/6)")

        # GATE-10 router_safety — the session-4 fixes (S4-1/S4-2/S4-4):
        # aggregation + negation hard-route to structural; a vague entity query
        # anchors to an agreement; an empty NOT-query carries an affirmation note.
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import retrieval_router as _rr
        agg_route = _rr.classify("how many obligations of each type")[0]
        neg_route = _rr.classify("which agreements have no governing law")[0]
        anchors = await cypher_exec.resolve_anchors(client, "tell me about the Xencor Aimmune deal")
        empty = await cypher_exec.validate_and_run(
            client, "MATCH (j:Jurisdiction) WHERE NOT ()-[:governed_by]->(j) RETURN j.name AS n",
            schema)
        # S6-3 reconciliation footer on a non-empty negation; S6-1 intent chain
        # guarantees the applies_principle (principle-layer) edge.
        neg_recon = await cypher_exec.validate_and_run(
            client, "MATCH (a:Agreement) WHERE NOT (a)-[:governed_by]->() RETURN a.name AS n", schema)
        rat = (await client.execute_cypher(
            "MATCH (r:Decision_Rationale)-[:applies_principle]->() RETURN r.grace_id AS g LIMIT 1"))["result"]
        chain = await cypher_exec.intent_chain(client, [rat[0]["g"]]) if rat else []
        checks = {
            "agg→structural": agg_route == "structural",
            "neg→structural": neg_route == "structural",
            "vague→anchored": len(anchors) >= 1,
            "empty-set note": bool(empty.get("empty_set_note")),
            "negation-reconcile": bool(neg_recon.get("reconciliation")),  # S6-3
            "intent-chain principle": any(e["rel"] == "applies_principle" for e in chain),  # S6-1
        }
        record("GATE-10 router_safety", all(checks.values()),
               ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in checks.items()))

    await client.aclose()
    return _summary()


def _summary() -> int:
    passed = sum(1 for _, s, _ in _results if s == "PASS")
    failed = sum(1 for _, s, _ in _results if s == "FAIL")
    skipped = sum(1 for _, s, _ in _results if s == "SKIP")
    print(f"\nGOLDEN GATE: {passed} PASS / {failed} FAIL / {skipped} SKIP")
    return 1 if failed else 0


def main() -> None:
    route_logs_to_stderr()
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
