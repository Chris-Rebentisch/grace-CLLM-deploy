#!/usr/bin/env python3
"""STEP 3 (propose-time, heat-free) — Map CQ coverage onto a Claude-authored proposal.

Replaces what the old CQ-merge coverage step did, but at propose time and WITHOUT
gpt-oss. Embeds the authored CQs and each proposal type/relationship with
nomic-embed-text (light, ~370 MB), assigns each CQ to the types/relationships that
answer it by cosine similarity, and writes the result back into the SeedSchema:

  * entity_types[].answerable_cqs   <- CQs each type answers
  * relationships[].answerable_cqs  <- CQs each relationship answers
  * coverage_matrix[]               <- per-CQ {cq_id, cq_text, covered_by_types,
                                       covered_by_relationships, coverage_status}
  * quality_metrics.cq_coverage_rate

So the review screen shows which CQ every type/relationship answers, and which CQs
are still uncovered — the one genuinely useful artifact the merge produced, now done
on the strong-model path with no heat and no merge stage.

Usage:
  python3 map_coverage.py --in ./workspace/seed_schema.json --domain legal
  python3 map_coverage.py --in ./workspace/seed_schema.json --cqs ./workspace/cqs.json
  python3 map_coverage.py --in ./workspace/seed_schema.json --domain legal --threshold 0.5
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import numpy as np

from _common import add_grace_to_path, get_session


def _load_cqs_from_db(grace_root: str | None, domain: str) -> list[dict]:
    db = get_session(grace_root)
    from src.discovery.cq_database import CompetencyQuestionRow  # noqa: E402

    rows = (
        db.query(CompetencyQuestionRow)
        .filter(CompetencyQuestionRow.domain == domain,
                CompetencyQuestionRow.status != "REJECTED")
        .all()
    )
    return [{"id": str(r.id), "text": r.canonical_text} for r in rows]


def _load_cqs_from_file(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for i, c in enumerate(raw):
        t = (c.get("canonical_text") or c.get("question") or "").strip()
        if t:
            out.append({"id": c.get("id", f"cq{i:03d}"), "text": t})
    return out


def _type_text(t: dict) -> str:
    return f"{t.get('name','')}: {t.get('description','')} {t.get('plain_description','')}".strip()


def _rel_text(r: dict) -> str:
    return (f"{r.get('name','')} ({r.get('source_type','')} -> {r.get('target_type','')}): "
            f"{r.get('description','')} {r.get('plain_description','')}").strip()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--grace-root", default=None)
    ap.add_argument("--in", dest="infile", required=True, help="Claude-authored seed_schema.json")
    ap.add_argument("--out", default=None, help="Output path (default: overwrite --in)")
    ap.add_argument("--domain", default=None, help="Pull CQs from DB for this domain")
    ap.add_argument("--cqs", default=None, help="OR read CQs from this cqs.json")
    ap.add_argument("--threshold", type=float, default=0.5, help="Cosine threshold for a match")
    ap.add_argument("--top-k", type=int, default=3, help="Max types/rels assigned per CQ")
    ap.add_argument("--base-url", default="http://localhost:11434")
    args = ap.parse_args()

    add_grace_to_path(args.grace_root)
    from src.shared.embeddings import embed_texts, cosine_similarity  # noqa: E402

    seed = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    types = seed.get("entity_types", [])
    rels = seed.get("relationships", [])

    if args.cqs:
        cqs = _load_cqs_from_file(args.cqs)
    elif args.domain:
        cqs = _load_cqs_from_db(args.grace_root, args.domain)
    else:
        raise SystemExit("[coverage] provide --domain (DB) or --cqs (file)")
    if not cqs:
        raise SystemExit("[coverage] no CQs found")
    print(f"[coverage] {len(cqs)} CQs, {len(types)} types, {len(rels)} relationships -> embedding (nomic-embed)")

    cq_texts = [c["text"] for c in cqs]
    type_texts = [_type_text(t) for t in types]
    rel_texts = [_rel_text(r) for r in rels]
    all_vecs = asyncio.run(embed_texts(cq_texts + type_texts + rel_texts, args.base_url))
    vecs = np.array(all_vecs, dtype=float)
    n_cq, n_t = len(cqs), len(types)
    cq_v = vecs[:n_cq]
    type_v = vecs[n_cq:n_cq + n_t]
    rel_v = vecs[n_cq + n_t:]

    # reset coverage fields
    for t in types:
        t["answerable_cqs"] = []
    for r in rels:
        r["answerable_cqs"] = []

    coverage_matrix = []
    covered = 0
    for i, c in enumerate(cqs):
        cov_types, cov_rels = [], []
        if n_t:
            sims = cosine_similarity(cq_v[i], type_v)
            for j in np.argsort(sims)[::-1][:args.top_k]:
                if sims[j] >= args.threshold:
                    cov_types.append(types[j]["name"])
                    types[j]["answerable_cqs"].append(c["text"])
        if len(rels):
            sims = cosine_similarity(cq_v[i], rel_v)
            for j in np.argsort(sims)[::-1][:args.top_k]:
                if sims[j] >= args.threshold:
                    cov_rels.append(rels[j]["name"])
                    rels[j]["answerable_cqs"].append(c["text"])
        if cov_types and cov_rels:
            status = "covered"; covered += 1
        elif cov_types or cov_rels:
            status = "partial"; covered += 1
        else:
            status = "uncovered"
        coverage_matrix.append({
            "cq_id": c["id"][:8], "cq_text": c["text"], "domain": args.domain or "other",
            "covered_by_types": cov_types, "covered_by_relationships": cov_rels,
            "coverage_status": status,
        })

    seed["coverage_matrix"] = coverage_matrix
    qm = seed.get("quality_metrics") or {}
    qm["cq_coverage_rate"] = round(covered / len(cqs), 3)
    seed["quality_metrics"] = qm

    out = args.out or args.infile
    Path(out).write_text(json.dumps(seed, indent=2), encoding="utf-8")
    uncovered = [m["cq_text"] for m in coverage_matrix if m["coverage_status"] == "uncovered"]
    print(f"[coverage] coverage_rate={qm['cq_coverage_rate']}  "
          f"covered/partial={covered}/{len(cqs)}  uncovered={len(uncovered)}")
    for u in uncovered[:10]:
        print(f"  UNCOVERED: {u[:90]}")
    print(f"[coverage] wrote {out} — ready for import_proposal.py")


if __name__ == "__main__":
    main()
