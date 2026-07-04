#!/usr/bin/env python3
"""STEP 6 (persist, no LLM) — Write a Claude-authored graph extraction into ArcadeDB,
with three-layer entity resolution so the same real-world entity converges to ONE vertex
across many documents / sessions (built for multi-shot extraction, not one-shot).

Resolution order per entity (most specific first):
  Layer 1  registry      — workspace/entity_registry.json: canonical (type,name)->grace_id
                           we've already minted (fast short-circuit + extractor memory).
  Layer 3  embedding ANN — grace `vectorNeighbors('{Type}[_embedding]', vec, k)` (D445);
                           reuse a neighbor with cosine similarity >= --er-threshold.
                           Catches NAME VARIANTS ("Siemens AG" ~ "Siemens Aktiengesellschaft")
                           WITHOUT the extractor needing a global view.
  Layer 2  exact/alias    — grace's own canonical_lookup at insert (name OR alias; the
                           Layer-2b grace edit makes aliases count). Backstop.

After resolve: new entities are inserted WITH their `_embedding` (so future docs can ANN
to them), every surface name is recorded as an alias (Layer 2a), and the registry updated.

Reuses grace: ArcadeClient, append_entity_alias, embed_texts (nomic-embed — light, no heat).

Usage:
  python3 import_extraction.py --in ./workspace/extraction_<doc>.json --doc-id <UUID> --module legal
  python3 import_extraction.py --in ... --doc-id <UUID> --dry-run          # show resolution plan
  python3 import_extraction.py --in ... --doc-id <UUID> --er-threshold 0.90 --no-resolve

Assumes each type carries a `name` property (this script injects properties.name). Embedding
text = the entity name. Conservative merge bias (default 0.90) per grace's D86.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from _common import add_grace_to_path

DEFAULT_REGISTRY = "workspace/entity_registry.json"

# Per-type ANN merge thresholds (mirrors grace's er_thresholds, D86 conservative bias).
# Jurisdictions/Territories tolerate variants → lower (so "Texas"~"State of Texas" 0.89
# merges). Products/Entities/Agreements need high → avoid false merges (two banks'
# "common stock offering" embed at ~0.89). Unlisted types fall back to --er-threshold.
# F-007 / ISS-0017 (validation run): name-only embeddings of SHORT names cluster
# tightly — a pre-fix false pair ("Bennett & Cole LLP" vs "Ridgeline Grading & Excavation
# Inc.") measured 0.9805. Legal_Entity raised 0.90→0.93 and Person 0.93→0.95 as minimal
# hardening; embedding TEXT composition deliberately unchanged (changing it would shift
# the embedding space). Near-threshold merges are additionally warned below.
TYPE_MERGE_THRESHOLDS = {
    "Jurisdiction": 0.85, "Territory": 0.85,
    "Legal_Entity": 0.93, "Person": 0.95,
    "Agreement": 0.95, "Amendment": 0.95,
    "Product": 0.92, "IP_Asset": 0.90,
    "Payment_Term": 0.92, "Obligation": 0.92, "Milestone": 0.90, "Exhibit": 0.92,
}

# F-007 / ISS-0017: any merge decided within this margin of its threshold is flagged
# "near-threshold merge — verify" so residual false merges stay visible to the operator.
NEAR_THRESHOLD_MARGIN = 0.02


def _post(url: str, body: dict, admin_key: str | None) -> dict:
    headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Graph-Scope": "all"}
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=120) as r:  # noqa: S310
        return json.loads(r.read().decode())


def _reg_key(etype: str, name: str) -> str:
    return f"{etype}\t{name.strip().lower()}"


def _load_registry(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"entities": {}}


def _reg_lookup(reg: dict, etype: str, name: str) -> str | None:
    e = reg["entities"].get(_reg_key(etype, name))
    return e["grace_id"] if e else None


def _pure_cosine(a, b):
    """Plain cosine similarity; None when either vector is degenerate."""
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


def _authoritative_sim(query_vec, nb, extracted_name):
    """F2-03 (second-validation-run ledger, cf. F-08): NEVER trust vectorNeighbors'
    raw distance. ArcadeDB returns distance 0.0 for stale/un-reindexed vectors,
    which the old `1.0 - distance` line turned into similarity 1.00 and merged
    unrelated entities (a validation-run import pass: Nadia Flores->Edward Whitfield,
    Crestline Partners->Whitfield Land Development, ...). Mirrors the in-tree
    F-08 fix in src/extraction/entity_resolver.py::_authoritative_similarity:
    (1) recompute cosine client-side from the neighbor's own _embedding;
    (2) distance ~0.0 without an embedding is accepted only on an exact
    case-insensitive name match; (3) otherwise fall back to 1 - distance."""
    emb = nb.get("_embedding")
    if isinstance(emb, list) and emb:
        sim = _pure_cosine(query_vec, emb)
        if sim is not None:
            return sim
    distance = float(nb.get("distance", 1.0))
    if distance <= 1e-9:
        name = (nb.get("name") or "").strip().lower()
        if name and name == (extracted_name or "").strip().lower():
            return 1.0
        return None
    return 1.0 - distance


async def _ann_lookup(client, etype: str, vec, top_k: int, extracted_name: str = ""):
    """Mirror grace's Tier-2 ANN: vectorNeighbors → (grace_id, similarity, name)."""
    lit = "[" + ",".join(str(float(v)) for v in vec) + "]"
    sql = f"SELECT vectorNeighbors('{etype}[_embedding]', {lit}, {top_k}) AS neighbors"
    try:
        res = await client.execute_sql(sql)
    except Exception:  # no index / empty type / ANN failure → treat as new (D86 bias)
        return None, 0.0, None
    rows = res.get("result", [])
    if not rows or not rows[0].get("neighbors"):
        return None, 0.0, None
    best = (None, -1.0, None)
    for nb in rows[0]["neighbors"]:
        gid = nb.get("grace_id")
        if not gid or nb.get("_deprecated"):
            continue
        sim = _authoritative_sim(vec, nb, extracted_name)  # F2-03: no raw-distance trust
        if sim is None:
            continue
        if sim > best[1]:
            best = (gid, sim, nb.get("name"))
    return best


async def _resolve(entities, vecs, reg, default_threshold, top_k, no_resolve, review_floor):
    from src.graph.arcade_client import get_arcade_client  # noqa: E402

    out = [{"grace_id": None, "how": "new"} for _ in entities]
    client = get_arcade_client()
    try:
        for i, e in enumerate(entities):
            t, n = e["entity_type"], e["name"]
            gid = _reg_lookup(reg, t, n)
            if gid:
                out[i] = {"grace_id": gid, "how": "registry"}
                continue
            if not no_resolve:
                thr = TYPE_MERGE_THRESHOLDS.get(t, default_threshold)  # per-type
                gid, sim, mname = await _ann_lookup(client, t, vecs[i], top_k, extracted_name=n)
                if gid and sim >= thr:
                    out[i] = {"grace_id": gid, "how": f"ann {sim:.2f}>={thr}~{mname!r}"}
                    # F-007 / ISS-0017: merges decided within NEAR_THRESHOLD_MARGIN of
                    # the type threshold are the residual-false-merge risk band — flag
                    # them for operator verification (printed in main()).
                    if sim - thr < NEAR_THRESHOLD_MARGIN:
                        out[i]["near_threshold"] = (round(sim, 3), mname, thr)
                    continue
                # Review-band: a near-match below this type's merge threshold — candidate dup.
                if gid and sim >= review_floor:
                    out[i]["review"] = (round(sim, 3), mname, thr)
    finally:
        await _maybe_close(client)
    return out


async def _post_insert(entities, vecs, resolution):
    """Write _embedding for newly-created vertices + record surface names as aliases."""
    from src.graph.arcade_client import get_arcade_client  # noqa: E402
    from src.graph.entity_ops import append_entity_alias  # noqa: E402

    client = get_arcade_client()
    try:
        for i, e in enumerate(entities):
            gid = resolution[i].get("grace_id")
            if not gid:
                continue
            # Embed ONLY newly-created vertices — never overwrite an existing canonical
            # vertex's embedding with a variant's. Overwriting drifts the canonical toward
            # the last surface form and can cause spurious re-merges (e.g. a canonical
            # ending up with sim 1.00 to a different variant). Matched entities keep their
            # canonical embedding. SQL UPDATE — grace's path; OpenCypher can't serialize lists.
            if resolution[i].get("created"):
                lit = "[" + ",".join(str(float(v)) for v in vecs[i]) + "]"
                await client.execute_sql(
                    f"UPDATE {e['entity_type']} SET _embedding = {lit} WHERE grace_id = '{gid}'")
            await append_entity_alias(client, gid, e["name"])
    finally:
        await _maybe_close(client)


async def _maybe_close(client):
    for attr in ("close", "aclose"):
        fn = getattr(client, attr, None)
        if fn:
            try:
                await fn()
            except Exception:
                pass
            return


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grace-root", default=None)
    ap.add_argument("--in", dest="infile", required=True)
    ap.add_argument("--doc-id", default=None)
    ap.add_argument("--module", default=None)
    ap.add_argument("--api-base", default="http://127.0.0.1:8000")
    ap.add_argument("--ollama-url", default="http://localhost:11434")
    ap.add_argument("--admin-key", default=None)
    ap.add_argument("--er-threshold", type=float, default=0.90,
                    help="Default ANN merge threshold for types NOT in TYPE_MERGE_THRESHOLDS (per-type overrides apply first)")
    ap.add_argument("--ann-top-k", type=int, default=5)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--review-floor", type=float, default=0.80,
                    help="Log ANN near-matches in [floor, threshold) as candidate-duplicate flags")
    ap.add_argument("--no-resolve", action="store_true", help="Disable Layer-3 ANN (exact/registry only)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    add_grace_to_path(args.grace_root)
    from src.shared.embeddings import embed_texts  # noqa: E402

    payload = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    entities = payload.get("entities", [])
    in_rels = payload.get("relationships", [])
    event_id = str(uuid4())
    reg_path = Path(args.registry)
    reg = _load_registry(reg_path)

    if not entities:
        raise SystemExit("[extract] no entities to write.")

    # Embed entity names once (nomic-embed, light).
    texts = [e["name"] for e in entities]
    vecs = asyncio.run(embed_texts(texts, args.ollama_url))

    resolution = asyncio.run(_resolve(entities, vecs, reg, args.er_threshold, args.ann_top_k,
                                      args.no_resolve, args.review_floor))
    pre = [i for i, r in enumerate(resolution) if r["grace_id"]]
    to_insert = [i for i, r in enumerate(resolution) if not r["grace_id"]]
    reviews = [(entities[i]["name"], resolution[i]["review"]) for i in range(len(entities))
               if resolution[i].get("review")]  # review = (sim, matched_name, type_threshold)
    print(f"[extract] {len(entities)} entities, {len(in_rels)} rels (event={event_id[:8]}, doc={args.doc_id})")
    if pre:
        print(f"[extract] resolved before insert: {len(pre)} | "
              + "; ".join(f"{entities[i]['name']!r}<-{resolution[i]['how']}" for i in pre))
    else:
        print("[extract] resolved before insert: 0")
    print(f"[extract] new (to insert): {len(to_insert)}")
    for nm, (sim, mname, thr) in reviews:
        print(f"[extract] REVIEW-BAND candidate-dup: {nm!r} ~ {mname!r} sim={sim} (< {thr}; NOT merged)")
    # F-007 / ISS-0017: surface merges decided within NEAR_THRESHOLD_MARGIN of threshold.
    for i, r in enumerate(resolution):
        if r.get("near_threshold"):
            sim, mname, thr = r["near_threshold"]
            print(f"[extract] WARN near-threshold merge — verify: {entities[i]['name']!r} ~ "
                  f"{mname!r} sim={sim} (threshold {thr}, margin < {NEAR_THRESHOLD_MARGIN})")

    if args.dry_run:
        print("[extract] --dry-run: nothing written.")
        return

    # Phase A — insert NEW entities (grace canonical_lookup name|alias is the exact backstop).
    if to_insert:
        ents = []
        for i in to_insert:
            e = entities[i]
            props = dict(e.get("properties", {}))
            props.setdefault("name", e["name"])
            ents.append({"entity_type": e["entity_type"], "properties": props,
                         "extraction_confidence": float(e.get("confidence", 0.0)) or None,
                         "source_document_id": args.doc_id, "extraction_event_id": event_id,
                         "ontology_module": args.module, "evidence_origin": "document",
                         "sensitivity_tags": e.get("sensitivity_tags", "")})
        try:
            res = _post(f"{args.api_base}/api/graph/entities/bulk",
                        {"entities": ents, "relationships": [], "extraction_event_id": event_id,
                         "source_document_id": args.doc_id}, args.admin_key)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"[extract] entity insert failed: HTTP {exc.code} {exc.read().decode()}")
        for k, i in enumerate(to_insert):
            r = (res.get("entity_results") or [])[k]
            resolution[i]["grace_id"] = r.get("grace_id")
            matched = r.get("canonical_match")
            resolution[i]["how"] = "exact/alias match" if matched else "inserted"
            resolution[i]["created"] = bool(r.get("created")) and not matched
        cm = sum(1 for i in to_insert if resolution[i]["how"] == "exact/alias match")
        print(f"[extract] phase A: {len(to_insert)-cm} created, {cm} deduped via grace canonical_lookup (name|alias)")

    # Phase A.5 — embed new vertices + record surface-name aliases.
    asyncio.run(_post_insert(entities, vecs, resolution))

    # Update + persist the registry (Layer 1).
    for i, e in enumerate(entities):
        gid = resolution[i].get("grace_id")
        if gid:
            reg["entities"][_reg_key(e["entity_type"], e["name"])] = {
                "grace_id": gid, "canonical_name": e["name"], "entity_type": e["entity_type"]}
    reg_path.parent.mkdir(parents=True, exist_ok=True)
    reg_path.write_text(json.dumps(reg, indent=2), encoding="utf-8")

    # Phase B — relationships using resolved grace_ids. Edges go through the bulk API
    # (insert_relationship), which upserts on (source, type, target) server-side
    # (F-012+F-018 / ISS-0009) — this script never creates edges directly, so re-imports
    # and cross-document repeat assertions no longer produce duplicate parallel edges.
    gid_of = {(e["entity_type"], e["name"]): resolution[i].get("grace_id") for i, e in enumerate(entities)}
    rels, unresolved = [], []
    for r in in_rels:
        sid = gid_of.get((r["source_type"], r["source_name"]))
        tid = gid_of.get((r["target_type"], r["target_name"]))
        if not sid or not tid:
            unresolved.append((r.get("source_name"), r.get("target_name")))
            continue
        rels.append({"relationship_type": r["relationship_type"], "source_grace_id": sid,
                     "target_grace_id": tid, "properties": r.get("properties", {}),
                     "relationship_confidence": float(r.get("confidence", 0.0)) or None,
                     "source_document_id": args.doc_id, "extraction_event_id": event_id,
                     "ontology_module": args.module})
    if rels:
        try:
            res = _post(f"{args.api_base}/api/graph/entities/bulk",
                        {"entities": [], "relationships": rels, "extraction_event_id": event_id,
                         "source_document_id": args.doc_id}, args.admin_key)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"[extract] relationship insert failed: HTTP {exc.code} {exc.read().decode()}")
        print(f"[extract] phase B: relationships created={res.get('relationships_created')}, failed={res.get('relationships_failed')}")
    if unresolved:
        print(f"[extract] WARN unresolved relationship endpoints: {unresolved[:5]}")

    # Post-import: rebuild the retrieval indexes. The API keeps in-memory BM25/vector
    # indexes; after graph writes a stale index serves PHANTOM grace_ids (A1 probe
    # staleness trap). Rebuild failure is a WARNING — the import itself succeeded.
    try:
        res = _post(f"{args.api_base}/api/retrieval/build-indexes", {}, args.admin_key)
        print(f"[extract] retrieval indexes rebuilt: {json.dumps(res)[:200]}")
    except Exception as exc:  # noqa: BLE001 — degrade to manual fallback, never fail the import
        print(f"[extract] WARN retrieval index rebuild failed: {exc}")
        print(f"[extract]      rebuild manually: curl -s -X POST {args.api_base}/api/retrieval/build-indexes"
              + (" -H 'X-Admin-Key: <key>'" if args.admin_key else ""))

    print(f"[extract] DONE. registry: {len(reg['entities'])} canonical entities -> {reg_path}")


if __name__ == "__main__":
    main()
