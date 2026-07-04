#!/usr/bin/env python3
"""grace-gap-remediation-harness — APPLY follow-on (co-signals 3 & 4).

The qwen-gated half of the remediation rubric. Given a grounded + well-formed KGCL
proposal (co-signals 1 & 2, scored heat-free by remediation_score.py), this exercises:

  3. GAP-CLOSURE (closure-readiness) — HEAT-FREE. Parse the KGCL, apply the real
     `change_executor._apply_change_to_schema` mutate to the active schema_json, and
     assert the missing element (class/relationship/property) now EXISTS in the
     post-change schema. "Re-extraction WOULD now realize the gap-closing element."
     (True gap-closure is a schema-enables -> re-extraction-realizes loop — the
     orphan/missing fact lives in extraction_claims, not the schema — so v1 proves
     readiness, not a full re-detect. See references/remediation-harness-design.md §8.)

  4. NON-REGRESSION — QWEN HEAT. Run the real CQ non-regression gate
     (`run_non_regression_gate`) on the post-change schema. The gate calls
     get_provider() -> qwen2.5:7b (the campaign-swapped model; NEVER the 70B, which
     can't fit/pull on a 28GB box). Reports gate_passed + pass-rate band.

HEAT GUARD (load-bearing): before running co-signal 4 we assert the configured model
is NOT a too-big generation model (llama3.3 / 70b / gpt-oss) — only qwen* / a small
local model is allowed. The probe REFUSES rather than risk loading the 70B.

SANDBOX ONLY: runs against the `grace_test` sibling (db name must end `_test`). Never
the live `grace` ontology; never mutates ArcadeDB (we work at the schema_json level —
no DDL sync, no ratify).

Usage:
  python3 apply_probe.py --kgcl "create relationship 'affiliated_with'"          # 3 + 4
  python3 apply_probe.py --kgcl "..." --closure-only                              # 3 only (heat-free)
  python3 apply_probe.py --kgcl "..." --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import signal_probe as sp  # reuse _resolve_db_url + ollama_clean  # noqa: E402

# Models too big for a 28GB box — co-signal 4 REFUSES if the gate would load one.
_FORBIDDEN_MODELS = ("llama3.3", "llama3.1:70", "llama3:70", "70b", "gpt-oss", "mixtral:8x22")


def _heat_guard() -> tuple[bool, str]:
    """Assert the configured CQ-gate model is small enough to run locally."""
    add_grace_to_path()
    from src.shared.config import GraceSettings  # type: ignore
    import yaml

    model = ""
    try:
        cfg = yaml.safe_load(Path("config/discovery.yaml").read_text()) or {}
        model = str(cfg.get("llm", {}).get("model", ""))
    except Exception:  # noqa: BLE001
        pass
    low = model.lower()
    if any(f in low for f in _FORBIDDEN_MODELS):
        return False, (f"REFUSED: configured llm.model='{model}' is too big for this box "
                       f"(would load a forbidden model). Swap to qwen2.5:7b first.")
    return True, f"configured llm.model='{model}' (allowed)"


def _closure_readiness(kgcl: str, active_schema: dict) -> dict:
    """Co-signal 3 — HEAT-FREE. Mutate the schema and assert the new element exists."""
    from src.ontology.change_executor import _apply_change_to_schema
    from src.ontology.kgcl_parser import KGCLParseError, parse_kgcl

    try:
        parsed = parse_kgcl(kgcl)
    except KGCLParseError as e:
        return {"closure_ready": False, "reason": f"unparseable KGCL: {e.message}"}
    new_schema = _apply_change_to_schema(active_schema, parsed)
    name = parsed.target_name or parsed.property_name or parsed.entity_name
    kind = parsed.command_kind.value
    # Where the new element should now live, by command kind.
    ents = new_schema.get("entity_types", {})
    rels = new_schema.get("relationships", {})
    present = False
    if "class" in kind and "obsolete" not in kind:
        present = name in ents
    elif "relationship" in kind and "obsolete" not in kind:
        present = name in rels
    elif "property" in kind and "add" in kind:
        entity = parsed.entity_name or parsed.target_name
        present = entity in ents and name in ents.get(entity, {}).get("properties", {})
    else:
        # change_domain_range / obsolete / change_* — readiness = schema changed at all
        present = new_schema != active_schema
    return {"closure_ready": bool(present), "element": name, "command_kind": kind,
            "schema_changed": new_schema != active_schema, "new_schema": new_schema}


async def _non_regression(db, new_schema: dict, threshold: float = 0.90) -> dict:
    """Co-signal 4 — QWEN HEAT. Real CQ non-regression gate on the post-change schema."""
    from src.ontology.cq_test_runner import run_non_regression_gate

    res = await run_non_regression_gate(db, proposed_schema_json=new_schema, threshold=threshold)
    # Band, not the raw rate, in the headline (D120/D217 spirit) — raw kept for the log.
    rate = res.pass_rate
    band = "high" if rate >= 0.90 else "medium" if rate >= 0.6 else "low"
    return {"gate_passed": res.gate_passed, "pass_rate": rate, "pass_band": band,
            "passing": res.passing, "total_cqs": res.total_cqs, "threshold": threshold}


def probe(kgcl: str, closure_only: bool) -> dict:
    db_url = sp._resolve_db_url(None)
    if not db_url.rsplit("/", 1)[-1].split("?")[0].endswith("_test"):
        return {"error": f"sandbox-only; refusing db {db_url}"}
    add_grace_to_path()
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    eng = create_engine(db_url, pool_pre_ping=True)
    db = sessionmaker(bind=eng)()
    try:
        row = db.execute(text("SELECT schema_json FROM ontology_versions WHERE is_active=true LIMIT 1")).first()
        if not row:
            return {"error": "no active ontology_version in sandbox — run seed_gaps.py --cq-fixture"}
        active_schema = row[0]
        out: dict = {"kgcl": kgcl}
        # --- co-signal 3 (heat-free) ---
        out["gap_closure"] = _closure_readiness(kgcl, active_schema)
        new_schema = out["gap_closure"].pop("new_schema", active_schema)
        if closure_only:
            out["non_regression"] = {"status": "skipped (--closure-only)"}
            return out
        # F-AP1 (swarm 2026-06-22): if the KGCL is unparseable (well-formedness already
        # failed), don't pay the qwen CQ-gate cost — a malformed proposal can't be applied.
        if "unparseable" in (out["gap_closure"].get("reason") or ""):
            out["non_regression"] = {"status": "skipped (unparseable KGCL — fails well-formedness)"}
            return out
        # --- heat guard before co-signal 4 ---
        ok, msg = _heat_guard()
        out["heat_guard"] = msg
        if not ok:
            out["non_regression"] = {"status": "refused", "reason": msg}
            return out
        # --- co-signal 4 (qwen heat) ---
        out["non_regression"] = asyncio.run(_non_regression(db, new_schema))
        return out
    finally:
        db.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kgcl", required=True, help="the proposed KGCL command")
    ap.add_argument("--closure-only", action="store_true",
                    help="co-signal 3 only (heat-free); skip the qwen CQ-gate")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    route_logs_to_stderr(quiet=True)

    rep = probe(args.kgcl, args.closure_only)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
        return
    if rep.get("error"):
        print(f"✗ {rep['error']}")
        return
    gc = rep["gap_closure"]
    f3 = "✓" if gc.get("closure_ready") else "✗"
    print(f"[3] GAP-CLOSURE (readiness) {f3} element '{gc.get('element')}' "
          f"({gc.get('command_kind')}) present-after-mutate={gc.get('closure_ready')} [heat-free]")
    nr = rep["non_regression"]
    if nr.get("status"):
        print(f"[4] NON-REGRESSION · {nr['status']}" + (f" — {nr.get('reason','')}" if nr.get('reason') else ""))
    else:
        f4 = "✓" if nr["gate_passed"] else "✗"
        print(f"[4] NON-REGRESSION {f4} gate_passed={nr['gate_passed']} "
              f"pass_band={nr['pass_band']} ({nr['passing']}/{nr['total_cqs']} CQs) [qwen2.5:7b]")
    clean, hmsg = sp.ollama_clean()
    print(f"\n  heat: {hmsg}")


if __name__ == "__main__":
    main()
