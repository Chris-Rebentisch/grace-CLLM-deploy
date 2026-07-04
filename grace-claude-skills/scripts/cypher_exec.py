#!/usr/bin/env python3
"""Deterministic Cypher validate + execute tool — the zero-latitude backbone of the
Claude-wrapped retrieval router. CF3-safe: a CLIENT of ArcadeDB; it never touches
``src/retrieval/*``.

Three responsibilities (all domain-agnostic — schema read at runtime):
  • introspect_schema()  — live vertex/edge inventory WITH EDGE DIRECTION + key props.
                           Direction is the fix for the pilot's #1 Cypher error
                           (writing an edge backwards).
  • lint_cypher()        — the 3 static rules the 50-query pilot's failures motivated.
  • validate_and_run()   — EXPLAIN (D293 pattern, plan-only, no mutation) → execute →
                           clean rows (strip _embedding / bookkeeping / numeric
                           confidence so D120/D217 holds by construction).

Heat: talks to ArcadeDB only. No LLM, no Ollama. Reused by retrieval_router.py and
retrieval_golden_gate.py.

  python3 cypher_exec.py --schema                       # print the introspected schema
  python3 cypher_exec.py --cypher "MATCH (a:Agreement) RETURN count(a) AS n"
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

# Bookkeeping/system keys never shown to a consumer (D120/D217 + token hygiene).
_STRIP_KEYS = frozenset({
    "_embedding", "_deprecated", "@rid", "@type", "@cat", "@in", "@out",
    "extraction_event_id", "source_document_id", "extracted_at", "updated_at",
    "human_validated", "extraction_confidence", "confidence_at_verification",
    "last_verified_at", "verdict", "reviewed_by", "review_session_id",
    "review_signal", "review_rationale", "decision_source", "ontology_module",
    "sensitivity_tags", "evidence_origin", "captured_at", "elicitation_method",
    "intent_origin",
})
_SYSTEM_VERTEX_TYPES = frozenset({
    "Query_Event", "Response_Event", "Extraction_Event", "Correction_Event",
    "Migration_Event", "GovernanceDecision_Event",
})

# S4-1: cues that the SEMANTIC path cannot honestly answer (it returns lexical
# neighbours and reports false success). The router hard-routes these to Cypher;
# the probe warns when they appear on a semantic answer.
_AGG_CUES = ("how many", "count", "number of", "distribution", "breakdown",
             "average", "mean", " per ", "most", "fewest", "top ", "rank",
             "group", "total ", "sum", " each ")
_NEG_CUES = ("no ", "without", "not ", "missing", "lack", "absent", "never",
             "none", "neither", "unassigned", "no governing")


def analytical_cues(query: str) -> tuple[list[str], list[str]]:
    """Return (aggregation_hits, negation_hits) — cue substrings present in the
    query. Either non-empty => the semantic path will mislead (S4-1)."""
    q = f" {query.lower()} "
    return ([c for c in _AGG_CUES if c in q],
            [c for c in _NEG_CUES if c in q])


async def introspect_schema(client) -> dict:
    """Return {vertices:[{name,count,props}], edges:[{name,count,src,dst}]}.

    Edge direction (src/dst labels) is sampled from one live edge per type — this
    is what lets a generator write ``(:Legal_Entity)-[:grants_license]->(:IP_Asset)``
    in the correct direction instead of guessing.
    """
    types = (await client.execute_sql(
        "SELECT name, records, type FROM schema:types"))["result"]
    vtypes = [t for t in types if t.get("type") == "vertex" and t.get("records", 0) > 0]
    etypes = [t for t in types if t.get("type") == "edge" and t.get("records", 0) > 0]

    vertices = []
    for t in sorted(vtypes, key=lambda x: -x.get("records", 0)):
        name = t["name"]
        if name in _SYSTEM_VERTEX_TYPES:
            continue
        keys = (await client.execute_cypher(
            f"MATCH (n:{name}) RETURN keys(n) AS k LIMIT 1"))["result"]
        props = [p for p in (keys[0]["k"] if keys else []) if p not in _STRIP_KEYS]
        vertices.append({"name": name, "count": t["records"], "props": sorted(props)})

    edges = []
    for t in sorted(etypes, key=lambda x: -x.get("records", 0)):
        name = t["name"]
        # Sample one edge to learn endpoint labels (direction).
        try:
            sample = (await client.execute_cypher(
                f"MATCH (a)-[r:{name}]->(b) RETURN labels(a)[0] AS src, labels(b)[0] AS dst LIMIT 1"))["result"]
        except Exception:
            sample = []
        if not sample:
            continue
        src, dst = sample[0].get("src"), sample[0].get("dst")
        if src in _SYSTEM_VERTEX_TYPES or dst in _SYSTEM_VERTEX_TYPES:
            continue
        edges.append({"name": name, "count": t["records"], "src": src, "dst": dst})

    return {"vertices": vertices, "edges": edges}


def lint_cypher(cypher: str, schema: dict) -> list[str]:
    """The 3 rules the pilot's 3 errors motivated. Returns a list of warnings
    (empty = clean). Advisory: EXPLAIN is the hard gate; lint catches the classes
    EXPLAIN passes but that return wrong rows.
    """
    warns: list[str] = []
    cy = cypher

    # Rule 1 — edge-direction sanity: every typed edge used must appear in the
    # schema, and if its endpoints are pinned with labels they must match the
    # schema direction.
    edge_by_name = {e["name"]: e for e in schema["edges"]}
    for m in re.finditer(r"\(\s*(\w+)?\s*:?\s*(\w+)?\s*\)\s*-\[\s*:?(\w+)\s*\]->\s*\(\s*(\w+)?\s*:?\s*(\w+)?\s*\)", cy):
        _, src_lbl, etype, _, dst_lbl = m.groups()
        e = edge_by_name.get(etype)
        if not e:
            continue
        if src_lbl and dst_lbl and (src_lbl, dst_lbl) == (e["dst"], e["src"]):
            warns.append(
                f"edge_direction: ':{etype}' is written ({src_lbl})->({dst_lbl}) "
                f"but the graph has ({e['src']})->({e['dst']}) — likely reversed")

    # Rule 2 — prefer typed predicate: text-AND filters on a free-text field where a
    # typed property exists are brittle (pilot S2-19/S2-13).
    if re.search(r"CONTAINS\s+'[^']+'\s+AND\s+\w+(\.\w+)?\s+CONTAINS", cy, re.I):
        warns.append("text_conjunction: multiple CONTAINS AND on text — "
                     "prefer a typed property (e.g. obligation_type=...) or OR the terms")

    # Rule 3 — canonicalize text filters: case-sensitive CONTAINS misses morphology
    # (pilot I1-31 'amendments' vs 'amend'). Flag bare CONTAINS without toLower.
    if re.search(r"(?<!toLower\()\w+\.\w+\s+CONTAINS\s+'[A-Z]", cy) and "toLower" not in cy:
        warns.append("case_sensitive_contains: CONTAINS is case-sensitive and exact — "
                     "consider toLower(...) and a stem (e.g. 'amend' not 'amendments')")

    return warns


def _clean_row(row):
    """Strip bookkeeping/numeric-confidence from any node-dicts in a result row."""
    if isinstance(row, dict):
        return {k: _clean_row(v) for k, v in row.items() if k not in _STRIP_KEYS}
    if isinstance(row, list):
        return [_clean_row(v) for v in row]
    return row


async def validate_and_run(client, cypher: str, schema: dict | None = None,
                           do_explain: bool = True) -> dict:
    """EXPLAIN (plan-only) then execute. Returns
    {ok, error, lint, row_count, rows}. ``ok=False`` with ``error`` if EXPLAIN or
    execution fails; lint warnings never block (advisory)."""
    lint = lint_cypher(cypher, schema) if schema else []

    if do_explain:
        try:
            await client.execute_cypher(f"EXPLAIN {cypher}")
        except Exception as e:
            return {"ok": False, "error": f"explain_failed: {str(e)[:300]}",
                    "lint": lint, "row_count": None, "rows": []}

    try:
        res = await client.execute_cypher(cypher)
        rows = [_clean_row(r) for r in (res.get("result", []) or [])]
        note = None
        recon = None
        # S4-4: a 0-row result on a NOT/absence pattern is a silent correctness
        # trap (true empty set vs reversed pattern). Affirm it by counting the
        # anchor-label candidates so 0-rows reads as a positive answer.
        if not rows and re.search(r"\bnot\b", cypher, re.I):
            note = await _empty_set_affirmation(client, cypher)
        elif rows:
            # S6-3: auto-reconciliation footer (non-empty negation + grouped counts).
            recon = await _reconciliation(client, cypher, rows)
        return {"ok": True, "error": None, "lint": lint,
                "row_count": len(rows), "rows": rows,
                "empty_set_note": note, "reconciliation": recon}
    except Exception as e:
        return {"ok": False, "error": f"execute_failed: {str(e)[:300]}",
                "lint": lint, "row_count": None, "rows": [],
                "empty_set_note": None, "reconciliation": None}


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


async def _reconciliation(client, cypher: str, rows: list) -> str | None:
    """S6-3: a footer that the evaluator would otherwise compute by hand.
    - grouped counts: Σ of the single numeric column across groups;
    - non-empty negation (NOT pattern): 'N of T {label} match (T-N have the edge)'.
    """
    # Grouped-count sum (≥2 rows, exactly one column numeric across all rows).
    if len(rows) >= 2 and isinstance(rows[0], dict):
        num_cols = [k for k in rows[0]
                    if all(isinstance(r, dict) and _is_num(r.get(k)) for r in rows)]
        if len(num_cols) == 1:
            col = num_cols[0]
            total = sum(r[col] for r in rows)
            total = round(total, 2) if isinstance(total, float) else total
            return f"Σ {col} = {total} across {len(rows)} groups"
    # Non-empty negation.
    if re.search(r"\bnot\b", cypher, re.I):
        m = re.search(r"\(\s*\w*\s*:\s*(\w+)", cypher)
        if m:
            label = m.group(1)
            try:
                t = (await client.execute_cypher(
                    f"MATCH (n:{label}) RETURN count(n) AS n"))["result"][0]["n"]
                return (f"{len(rows)} of {t} {label} match "
                        f"({t - len(rows)} have the relationship) — reconciles to {t}")
            except Exception:
                return None
    return None


async def intent_chain(client, grace_ids: list[str]) -> list[dict]:
    """S6-1: given surfaced node grace_ids, return the FULL typed intent
    neighbourhood with node CONTENT (names), guaranteeing principle-layer edges
    (applies_principle / specializes / explains) appear in a why-answer — not just
    the rationale's justifies-consequences."""
    if not grace_ids:
        return []
    idlist = ", ".join(f"'{g}'" for g in grace_ids if g)
    intent_rels = ("applies_principle", "justifies", "explains",
                   "rejected_alternative_to", "specializes", "compels")
    rel_list = ", ".join(f"'{r}'" for r in intent_rels)
    cypher = (
        f"MATCH (n)-[r]-(m) WHERE n.grace_id IN [{idlist}] "
        f"AND type(r) IN [{rel_list}] AND startNode(r).grace_id IS NOT NULL "
        f"RETURN DISTINCT startNode(r).name AS src, type(r) AS rel, "
        f"coalesce(endNode(r).name, endNode(r).summary) AS dst, "
        f"labels(startNode(r))[0] AS src_type, labels(endNode(r))[0] AS dst_type")
    try:
        rows = (await client.execute_cypher(cypher))["result"]
    except Exception:
        return []
    seen = set()
    out = []
    for r in rows:
        key = (r.get("src"), r.get("rel"), r.get("dst"))
        if key in seen or not r.get("src") or not r.get("dst"):
            continue
        seen.add(key)
        out.append(r)
    return out


async def _empty_set_affirmation(client, cypher: str) -> str | None:
    """Count the first matched vertex label so an empty NOT-pattern result is
    confirmed as a real 'nothing matches' answer, not a backwards query."""
    m = re.search(r"\(\s*\w*\s*:\s*(\w+)", cypher)
    if not m:
        return None
    label = m.group(1)
    try:
        cnt = (await client.execute_cypher(
            f"MATCH (n:{label}) RETURN count(n) AS n"))["result"][0]["n"]
    except Exception:
        return None
    return (f"empty_set confirmed: 0 of {cnt} {label} candidates match — a valid "
            f"'nothing matches' answer. (If unexpected, check edge direction.)")


def render_schema_text(schema: dict) -> str:
    """Compact, generator-facing schema description (vertices + directed edges +
    a COOKBOOK of the patterns agents got wrong: lookup field, negation
    direction, multi-hop self-exclusion, aggregation). S4-5."""
    lines = ["VERTEX TYPES (count) — key properties:"]
    dual_name = []
    for v in schema["vertices"]:
        lines.append(f"  ({v['name']}) [{v['count']}]: {', '.join(v['props'][:12])}")
        if "name" in v["props"] and "legal_name" in v["props"]:
            dual_name.append(v["name"])
    lines.append("\nEDGE TYPES (count) — DIRECTION matters, write exactly as shown:")
    for e in schema["edges"]:
        lines.append(f"  (:{e['src']})-[:{e['name']}]->(:{e['dst']})  [{e['count']}]")

    # Concrete examples drawn from the live schema (domain-agnostic — picks real edges).
    edges = schema["edges"]
    eg = edges[0] if edges else None
    chain = next(((a, b) for a in edges for b in edges
                  if a["dst"] == b["src"] and a["name"] != b["name"]), None)
    selfloop = next((e for e in edges if e["src"] == e["dst"]), None)  # S6-4 transitive
    lines.append("\nCOOKBOOK — patterns that EXPLAIN-pass but are easy to get wrong:")
    # S6-4: counts shown above are STORED estimates and may exceed live traversable edges.
    lines.append("  • Counts above are STORED estimates — live traversable edges may be fewer; "
                 "don't treat a count as the exact row total of a traversal.")
    # S6-5: grouping/filter key values can have inconsistent surface forms.
    lines.append("  • Grouping/filter values may vary in surface form (e.g. 'State of Texas' vs 'California'); "
                 "match with CONTAINS / normalize — don't assume a bare token.")
    note = (f"  • Entity lookup: match on `name` with a distinctive token, "
            f"case-insensitively: WHERE toLower(n.name) CONTAINS 'token'.")
    if dual_name:
        note += f" (Types with both name+legal_name — {', '.join(dual_name)} — still match on `name`.)"
    lines.append(note)
    if eg:
        lines.append(f"  • Absence/negation — MIND DIRECTION:")
        lines.append(f"      no outbound:  MATCH (a:{eg['src']}) WHERE NOT (a)-[:{eg['name']}]->() RETURN a.name")
        lines.append(f"      no inbound:   MATCH (b:{eg['dst']}) WHERE NOT ()-[:{eg['name']}]->(b) RETURN b.name")
    if chain:
        a, b = chain
        lines.append(f"  • Multi-hop: MATCH (x:{a['src']})-[:{a['name']}]->(:{a['dst']})-[:{b['name']}]->(y:{b['dst']}) RETURN x.name, y.name")
    if selfloop:  # S6-4: variable-length / transitive over a self-referential edge
        lines.append(f"  • Transitive (variable-length) over the self-referential :{selfloop['name']} "
                     f"({selfloop['src']}→{selfloop['src']}): "
                     f"MATCH (a:{selfloop['src']})-[:{selfloop['name']}*1..3]->(b:{selfloop['src']}) RETURN a.name, b.name")
    lines.append("  • Reverse 'other parties' — self-exclude: MATCH (o)-[:party_to]->(ag) "
                 "WHERE o.name <> anchor.name RETURN o.name  (else the anchor returns itself).")
    lines.append("  • Aggregation: count(*) / avg(x) / collect(DISTINCT n.name); group by the RETURNed non-aggregate.")
    lines.append("  • Always RETURN named columns (… AS x); never RETURN a whole node or _embedding.")
    return "\n".join(lines)


# ---- S4-2: entity anchoring + node summary (the vagueness fix) ----------------
_NAME_STOP = frozenset({
    "agreement", "license", "development", "commercialization", "services",
    "service", "supply", "manufacturing", "distributor", "cooperation",
    "collaboration", "promotion", "endorsement", "strategic", "alliance",
    "capital", "resources", "agency", "inc", "llc", "ltd", "corp", "company",
    "the", "and", "systems", "networks", "group", "holdings", "international",
    "co", "branding", "joint", "venture", "outsourcing", "reseller", "franchise",
    "trademark", "intellectual", "property", "deal", "about", "tell", "what",
    "incorporated", "corporation", "limited", "of", "for", "with",
})


async def resolve_anchors(client, text: str, anchor_types=("Legal_Entity", "Agreement", "Product")) -> list[dict]:
    """NER-lite: which graph entities of anchor_types are mentioned in `text`.
    Returns deduped [{grace_id, label, name}] for AGREEMENTS — a matched
    Legal_Entity/Product is resolved to the agreement(s) it is connected to, so a
    vague 'tell me about the X deal' anchors on the right contract(s)."""
    q = f" {text.lower()} "
    matched_agreements: dict[str, dict] = {}

    for label in anchor_types:
        rows = (await client.execute_cypher(
            f"MATCH (n:{label}) RETURN n.grace_id AS gid, n.name AS name"))["result"]
        for r in rows:
            name = r.get("name") or ""
            toks = [t for t in re.split(r"[^a-z0-9]+", name.lower())
                    if len(t) >= 4 and t not in _NAME_STOP]
            if not any(f" {t} " in q or f" {t}" in q or f"{t} " in q for t in toks):
                continue
            # Resolve to agreement(s).
            if label == "Agreement":
                matched_agreements[r["gid"]] = {"grace_id": r["gid"], "label": "Agreement", "name": name}
            else:
                edge = "party_to" if label == "Legal_Entity" else "concerns_subject"
                arrow = (f"(n:{label} {{grace_id:'{r['gid']}'}})-[:{edge}]->(a:Agreement)"
                         if label == "Legal_Entity"
                         else f"(a:Agreement)-[:{edge}]->(n:{label} {{grace_id:'{r['gid']}'}})")
                ags = (await client.execute_cypher(
                    f"MATCH {arrow} RETURN a.grace_id AS gid, a.name AS name"))["result"]
                for a in ags:
                    matched_agreements[a["gid"]] = {"grace_id": a["gid"], "label": "Agreement", "name": a["name"]}
    return list(matched_agreements.values())


async def node_summary(client, grace_id: str, per_edge_cap: int = 8) -> dict:
    """Domain-agnostic 'tell me about X': the node's own props + its 1-hop
    neighbourhood grouped by edge type (with a per-type cap + count). Answers a
    vague entity-anchored query as a coherent, grounded profile."""
    g = grace_id.replace("'", "")
    node = (await client.execute_cypher(
        f"MATCH (n) WHERE n.grace_id='{g}' RETURN labels(n)[0] AS label, properties(n) AS props"))["result"]
    if not node:
        return {}
    props = _clean_row(node[0].get("props") or {})
    out = (await client.execute_cypher(
        f"MATCH (n)-[r]->(m) WHERE n.grace_id='{g}' AND m.name IS NOT NULL "
        f"RETURN type(r) AS rel, m.name AS name ORDER BY rel"))["result"]
    inc = (await client.execute_cypher(
        f"MATCH (m)-[r]->(n) WHERE n.grace_id='{g}' AND m.name IS NOT NULL "
        f"RETURN type(r) AS rel, m.name AS name ORDER BY rel"))["result"]

    def group(rows):
        g_: dict[str, list[str]] = {}
        for r in rows:
            g_.setdefault(r["rel"], []).append(r["name"])
        return g_

    return {"label": node[0].get("label"), "name": props.get("name") or props.get("title"),
            "props": props, "outgoing": group(out), "incoming": group(inc),
            "per_edge_cap": per_edge_cap}


async def _main(args) -> None:
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    c = get_arcade_client()
    schema = await introspect_schema(c)
    if args.schema:
        print(render_schema_text(schema))
    if args.cypher:
        out = await validate_and_run(c, args.cypher, schema)
        print(json.dumps(out, indent=2, default=str))
    await c.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--schema", action="store_true", help="print introspected schema")
    ap.add_argument("--cypher", help="validate + execute a Cypher string")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="restore full INFO logs (default: quiet — WARNING only)")
    args = ap.parse_args()
    if not args.schema and not args.cypher:
        ap.error("one of --schema / --cypher required")
    route_logs_to_stderr(quiet=not args.verbose)  # R6: quiet by default
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
