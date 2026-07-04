#!/usr/bin/env python3
"""STEP 5 (prep, no LLM) — Prepare the skeleton proposal for Claude property-detailing.

After the operator reviews the skeleton proposal at /review, this pulls the types
that should be detailed (default: all of them; with --only-accepted: just the ones
the operator accepted in the review session) and writes a slimmed seed_schema for
Claude to enrich with full properties. This is the "detail properties on the ratified
subset only" step (refined Option B) — done in Claude, not gpt-oss.

Usage:
  # detail everything in the skeleton proposal:
  python3 export_proposal_for_detailing.py --in ./workspace/seed_schema.json
  # detail only the types the operator accepted in a review session:
  python3 export_proposal_for_detailing.py --in ./workspace/seed_schema.json \
      --session-id 5c903145-... --only-accepted

Output: ./workspace/to_detail.json  (same SeedSchema shape, skeleton, kept subset)
Then: invoke grace-property-detailing; Claude writes seed_schema.detailed.json.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

# Decisions that mean "keep this element". Liberal on purpose; everything else drops.
_KEEP = {"accept", "accepted", "keep", "approve", "approved", "edit", "edited"}


def _get(url: str) -> object:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (localhost)
        return json.loads(r.read().decode())


def _accepted_names(api_base: str, session_id: str) -> set[str]:
    """Names (entity types + relationships) the operator accepted in the session."""
    raw = _get(f"{api_base}/api/ontology/review/{session_id}/elements")
    items = raw if isinstance(raw, list) else [
        *(raw.get("entity_types") or []), *(raw.get("relationships") or [])
    ]
    kept: set[str] = set()
    for e in items:
        decision = str(e.get("decision", "")).lower()
        name = e.get("element_name") or e.get("name")
        if name and (decision in _KEEP):
            kept.add(name)
    return kept


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="infile", required=True, help="Skeleton seed_schema.json")
    ap.add_argument("--out", default="./workspace/to_detail.json")
    ap.add_argument("--session-id", default=None, help="Review session to read decisions from")
    ap.add_argument("--only-accepted", action="store_true",
                    help="Keep only types/relationships accepted in --session-id")
    ap.add_argument("--api-base", default="http://127.0.0.1:8000")
    args = ap.parse_args()

    seed = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    ets = seed.get("entity_types", [])
    rels = seed.get("relationships", [])

    if args.only_accepted:
        if not args.session_id:
            raise SystemExit("[detail-prep] --only-accepted requires --session-id")
        kept = _accepted_names(args.api_base, args.session_id)
        ets = [t for t in ets if t.get("name") in kept]
        rels = [r for r in rels if r.get("name") in kept]
        print(f"[detail-prep] operator accepted {len(kept)} elements; "
              f"keeping {len(ets)} types / {len(rels)} relationships")
    else:
        print(f"[detail-prep] detailing all {len(ets)} types / {len(rels)} relationships")

    out = dict(seed)
    out["entity_types"] = ets
    out["relationships"] = rels
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[detail-prep] wrote {args.out} — invoke grace-property-detailing next.")


if __name__ == "__main__":
    main()
