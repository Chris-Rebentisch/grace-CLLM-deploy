#!/usr/bin/env python3
"""Run a structural battery through the deterministic Cypher path and score recall.

Each battery item carries a Claude-generated `cypher` (the LLM step) and an `expect`
substring. This runner is the deterministic half: validate + execute each Cypher via
cypher_exec, then score FOUND / MISS / GAP. It quantifies the structural-recall lift
the Claude-wrapped router delivers vs the semantic-only pipeline.

  expect="ANY"  -> FOUND if any row returns (used where the answer text is open)
  axis="extraction-gap" -> a MISS is expected (graph lacks the data; not a retrieval fault)

  python3 run_battery.py [--battery ../runs/retrieval-probe/structural_battery.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import cypher_exec  # noqa: E402

DEFAULT_BATTERY = str(Path(__file__).resolve().parent.parent
                      / "runs" / "retrieval-probe" / "structural_battery.json")


async def _run(path: str) -> int:
    spec = json.loads(Path(path).read_text())
    items = spec["queries"]
    add_grace_to_path()
    from src.graph.arcade_client import get_arcade_client
    c = get_arcade_client()
    schema = await cypher_exec.introspect_schema(c)

    rows_out = []
    for it in items:
        out = await cypher_exec.validate_and_run(c, it["cypher"], schema)
        blob = json.dumps(out["rows"], default=str).lower()
        exp = it["expect"]
        if not out["ok"]:
            verdict = "ERROR"
        elif exp == "ANY":
            verdict = "FOUND" if out["row_count"] else "MISS"
        else:
            verdict = "FOUND" if exp.lower() in blob else "MISS"
        rows_out.append({"id": it["id"], "axis": it["axis"], "verdict": verdict,
                         "rows": out["row_count"], "lint": out["lint"],
                         "ok": out["ok"], "q": it["q"]})
    await c.aclose()

    # Scoring: retrieval-axis recall is the headline; extraction-gap MISSes are
    # expected and excluded from the retrieval denominator.
    retr = [r for r in rows_out if r["axis"] == "retrieval"]
    gaps = [r for r in rows_out if r["axis"] == "extraction-gap"]
    found = sum(1 for r in retr if r["verdict"] == "FOUND")

    print("STRUCTURAL BATTERY — deterministic Cypher path (Claude-generated queries)\n")
    for r in rows_out:
        mark = {"FOUND": "✓", "MISS": "✗", "ERROR": "‼", "GAP": "—"}.get(r["verdict"], "?")
        tag = "" if r["axis"] == "retrieval" else f"  [{r['axis']}]"
        lint = f"  lint!={r['lint']}" if r["lint"] else ""
        print(f"  {mark} {r['id']:<6} {r['verdict']:<6} ({r['rows']}r){tag}  {r['q'][:58]}{lint}")

    print(f"\nRETRIEVAL-AXIS RECALL: {found}/{len(retr)} = {100*found/len(retr):.0f}%")
    if gaps:
        gap_found = sum(1 for r in gaps if r["verdict"] == "FOUND")
        print(f"EXTRACTION-GAP items: {gap_found}/{len(gaps)} returned data "
              f"(MISS here = graph lacks the fact, not a retrieval fault)")
    return found, len(retr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--battery", default=DEFAULT_BATTERY)
    args = ap.parse_args()
    route_logs_to_stderr()
    asyncio.run(_run(args.battery))


if __name__ == "__main__":
    main()
