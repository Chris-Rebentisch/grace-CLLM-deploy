#!/usr/bin/env python3
"""OPTIONAL (persist, no LLM) — Open a browsable grace /review record for a proposal.

On the Claude path the proposal is auto-accepted by Step 4 (auto_accept.py), so this is
NO LONGER REQUIRED — use it only if you want the grace /review screen for inspection.
It opens a read-only review session from a SeedSchema; it does not gate anything.

Claude (the LLM) writes seed_schema.json following templates/seed_schema.example.json
(the SeedSchema shape: entity_types[] + relationships[]). This script POSTs it to
the running FastAPI server's review-start route, which opens the /review UI screen.

It auto-discovers a real merge_run_id from GET /api/discovery/merge-latest unless
you pass --merge-run-id explicitly.

Prereqs:
  * uvicorn running (cd ~/grace && uvicorn src.api.main:app --port 8000)
  * GRACE_PERMISSION_ENFORCEMENT_ENABLED unset/0 (else review/start 403s with
    no_active_matrix — see the 2026-06-09 session log)

Usage:
  python3 import_proposal.py --in ./workspace/seed_schema.json --reviewer glenny
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4


def _post(url: str, body: dict, admin_key: str | None) -> dict:
    data = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "X-Graph-Scope": "all"}
    if admin_key:
        headers["X-Admin-Key"] = admin_key
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.loads(r.read().decode())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="infile", required=True, help="Claude-authored seed_schema.json")
    ap.add_argument("--reviewer", required=True, help="Reviewer name recorded on the session")
    ap.add_argument("--api-base", default="http://127.0.0.1:8000")
    ap.add_argument("--merge-run-id", default=None,
                    help="Optional traceability id; default = synthetic (merge is dropped on the Claude path)")
    ap.add_argument("--admin-key", default=None, help="X-Admin-Key if GRACE_ADMIN_KEY is set on the server")
    args = ap.parse_args()

    seed = json.loads(Path(args.infile).read_text(encoding="utf-8"))
    ets = seed.get("entity_types")
    rels = seed.get("relationships")
    if not isinstance(ets, list) or not isinstance(rels, list):
        raise SystemExit("[import-proposal] seed_schema.json needs list 'entity_types' and 'relationships'.")
    print(f"[import-proposal] proposal: {len(ets)} entity types, {len(rels)} relationships")

    # The CQ merge is dropped on the Claude path, so by default we synthesize the
    # traceability id. review/start (review_ops.start_review_session) stores
    # merge_run_id but NEVER dereferences it — it builds the session directly from
    # seed_schema_data, so any id is accepted. Pass --merge-run-id to link a specific run.
    merge_run_id = args.merge_run_id or f"claude-proposal-{uuid4()}"
    print(f"[import-proposal] merge_run_id = {merge_run_id}")

    body = {"merge_run_id": merge_run_id, "reviewer": args.reviewer, "seed_schema_data": seed}
    try:
        resp = _post(f"{args.api_base}/api/ontology/review/start", body, args.admin_key)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        raise SystemExit(f"[import-proposal] review/start failed: HTTP {exc.code} {detail}")

    session_id = resp.get("session_id") or resp.get("id")
    print(f"[import-proposal] review session created: {session_id}")
    print(f"[import-proposal] OPEN: {args.api_base.replace('8000','3000')}/review?session_id={session_id}")


if __name__ == "__main__":
    main()
