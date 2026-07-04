#!/usr/bin/env python3
"""STEP 4 (no LLM) — Auto-accept a Claude-authored ontology proposal.

Replaces the HUMAN review/ratify gate (operator decision 2026-06-10: skip human
ontology ratification on the Claude path; grace's /review + ratify CODE stays, we
just don't require a person to click through). This makes the proposal the ACTIVE
ontology version AND syncs the DDL into ArcadeDB, so graph extraction (Step 6) has
typed vertex/edge types to write against.

Two documented, standalone calls (no review session required — verified in source):
  1. POST /api/ontology/ratify     -> creates + activates the OntologyVersion (Postgres)
  2. POST /api/graph/sync-schema   -> generates DDL from the active version, executes on ArcadeDB

Input: the coverage-enriched seed_schema.json from grace-ontology-proposal.

Usage:
  python3 auto_accept.py --in ./workspace/seed_schema.json
  python3 auto_accept.py --in ./workspace/seed_schema.json --dry-run        # build payloads only
  python3 auto_accept.py --in ./workspace/seed_schema.json --no-sync        # ratify, skip ArcadeDB
  python3 auto_accept.py --in ./workspace/seed_schema.json --source manual --reviewer "auto (claude)"

PREREQS: uvicorn running; ArcadeDB up (for sync); GRACE_PERMISSION_ENFORCEMENT_ENABLED=0.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

# Projection allowlists mirror grace's review_ops._entity_type_to_schema /
# _relationship_to_schema so the ratified schema_json matches what ratify expects.
_ET_KEYS = ("description", "properties", "parent_type", "domain", "provenance", "confidence")
_REL_KEYS = ("source_type", "target_type", "description", "richness_tier",
             "edge_properties", "domain", "provenance", "confidence")


def _post(url: str, body: dict, admin_key: str | None) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json", "X-Graph-Scope": "all"}
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=180) as r:  # noqa: S310
        return json.loads(r.read().decode())


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310
        return json.loads(r.read().decode())


def _project(item: dict, keys) -> dict:
    return {k: item[k] for k in keys if k in item}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="infile", required=True, help="Coverage-enriched seed_schema.json")
    ap.add_argument("--api-base", default="http://127.0.0.1:8000")
    ap.add_argument("--reviewer", default="auto-accept (claude)",
                    help="Recorded on the version (provenance of the bypass)")
    ap.add_argument("--source", default="manual",
                    choices=["manual", "discovery", "adaptive_evolution", "guided_review"],
                    help="VersionSource (default 'manual' = accepted without human edit)")
    ap.add_argument("--admin-key", default=None, help="X-Admin-Key if GRACE_ADMIN_KEY is set")
    ap.add_argument("--default-domain", default=None,
                    help="Domain for elements that lack one (default: the single entity domain, else 'general')")
    ap.add_argument("--no-sync", action="store_true", help="Ratify only; skip ArcadeDB DDL sync")
    ap.add_argument("--dry-run", action="store_true", help="Build payloads; do not POST")
    args = ap.parse_args()

    seed = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    ets = seed.get("entity_types") or []
    rels = seed.get("relationships") or []
    if not ets:
        raise SystemExit("[auto-accept] seed_schema.json has no entity_types.")

    # Keep all elements in one module: relationships often omit `domain`, which would
    # scatter them into a separate "general" module from the types. Inherit a single
    # default (the lone entity domain when there is exactly one) so module-scoped
    # lookups stay coherent. schema_json still carries every element regardless.
    domains = {t.get("domain") for t in ets if t.get("domain")}
    default_domain = args.default_domain or (next(iter(domains)) if len(domains) == 1 else "general")
    for it in ets + rels:
        it.setdefault("domain", default_domain)

    schema_json = {
        "entity_types": {t["name"]: _project(t, _ET_KEYS) for t in ets},
        "relationships": {r["name"]: _project(r, _REL_KEYS) for r in rels},
    }
    # Partition by domain (mirror review_ops.partition_schema_by_module).
    modules: dict = {}
    for name, t in schema_json["entity_types"].items():
        modules.setdefault(t.get("domain", "general"), {"entity_types": {}, "relationships": {}})["entity_types"][name] = t
    for name, r in schema_json["relationships"].items():
        modules.setdefault(r.get("domain", "general"), {"entity_types": {}, "relationships": {}})["relationships"][name] = r

    rate = (seed.get("quality_metrics") or {}).get("cq_coverage_rate")
    body = {
        "schema_json": schema_json,
        "schema_modules": modules,
        "source": args.source,
        "reviewer": args.reviewer,
        "changelog": (f"Auto-accepted Claude-authored proposal: {len(ets)} types, "
                      f"{len(rels)} relationships, CQ coverage {rate}. "
                      f"Human ontology ratification bypassed (operator decision 2026-06-10)."),
        "cq_coverage_snapshot": seed.get("quality_metrics") or None,
    }
    print(f"[auto-accept] proposal: {len(ets)} types, {len(rels)} relationships across "
          f"{len(modules)} module(s) {list(modules)}; coverage_rate={rate}; source={args.source}")

    if args.dry_run:
        print("[auto-accept] --dry-run: not posting. ratify body preview:")
        print(json.dumps({k: (v if k != 'schema_json' else f'<{len(ets)} types / {len(rels)} rels>')
                          for k, v in body.items()}, indent=2)[:1200])
        return

    # 1) Ratify -> active ontology version
    try:
        version = _post(f"{args.api_base}/api/ontology/ratify", body, args.admin_key)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[auto-accept] ratify failed: HTTP {exc.code} {exc.read().decode()}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"[auto-accept] could not reach {args.api_base} ({exc}). Is uvicorn up?")
    print(f"[auto-accept] RATIFIED active ontology v{version.get('version_number')} "
          f"(id={version.get('id')}, types={version.get('entity_type_count')}, "
          f"rels={version.get('relationship_type_count')}, active={version.get('is_active')})")

    # 2) Sync DDL to ArcadeDB
    if args.no_sync:
        print("[auto-accept] --no-sync: skipped ArcadeDB DDL sync. Run POST /api/graph/sync-schema before extraction.")
        return
    try:
        rec = _post(f"{args.api_base}/api/graph/sync-schema", {}, args.admin_key)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"[auto-accept] ratified OK, but ArcadeDB sync failed: "
                         f"HTTP {exc.code} {exc.read().decode()}. Retry POST /api/graph/sync-schema once ArcadeDB is up.")
    stmts = rec.get("statements") or rec.get("ddl_statements") or []
    errs = [s for s in stmts if isinstance(s, dict) and s.get("error")]
    print(f"[auto-accept] ArcadeDB DDL synced: {len(stmts)} statement(s), {len(errs)} error(s).")
    if errs:
        for e in errs[:8]:
            print(f"  DDL ERROR: {str(e)[:160]}")

    # 3) Verify active
    try:
        active = _get(f"{args.api_base}/api/ontology/active")
        print(f"[auto-accept] VERIFY active = v{active.get('version_number')} "
              f"({active.get('entity_type_count')} types, {active.get('relationship_type_count')} rels). "
              f"Ready for graph extraction (Step 6).")
    except urllib.error.HTTPError as exc:
        print(f"[auto-accept] WARN could not verify active version: HTTP {exc.code}")


if __name__ == "__main__":
    main()
