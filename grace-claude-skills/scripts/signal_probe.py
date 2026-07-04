#!/usr/bin/env python3
"""grace-signal-probe — CLI client of the GrACE signal pipeline (roadmap A3).

Runs the six deterministic gap detectors (A–F) against a target database and
reports, per (signal, module): strength, FIRED vs QUIET, and the trend/evidence
the detector used. This is the A1/A2 "drive it live, read the output" discipline
applied to the self-monitoring layer.

DETERMINISTIC + HEAT-FREE: the signal pipeline is LLM-free (PromQL/SQL +
Mann-Kendall). We never call get_provider(). The probe spawns the pipeline's
sanctioned CLI entry point (D246 — out-of-process) with DATABASE_URL pointed at
the target DB, then reads back analytics_signals by run_id.

SUBSTRATE HONESTY (the #1 lesson): a detector that returns no record may be
either (a) correctly QUIET on a healthy module, or (b) NO-OP for lack of
substrate (empty Prometheus history / single-day claims / empty cq_test_runs).
The probe never conflates them — it reports the substrate each detector needs
and whether it was present.

Usage:
  python3 signal_probe.py                              # all detectors vs grace_test
  python3 signal_probe.py --signal B --signal D        # subset
  python3 signal_probe.py --module a3probe_orphan       # restrict to a module
  python3 signal_probe.py --db-url <url>               # target db (default grace_test)
  python3 signal_probe.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

GRACE_ROOT = os.path.expanduser("~/grace")
_GEN_MODELS = ("gpt-oss", "llama", "qwen", "mixtral", "mistral", "gemma")

# What substrate each detector reads — used for the substrate-honesty report.
SUBSTRATE = {
    "A": "Prometheus grace_extraction_triple_confidence (current vs 14d baseline)",
    "B": "extraction_claims co-occurrence (Postgres)",
    "C": "Prometheus grace_extraction_validation_failures_total",
    "D": "extraction_claims daily counts, Mann-Kendall >=10 days (Postgres)",
    "E": "Prometheus validation failures (domain/range)",
    "F": "cq_test_runs failure rate, Mann-Kendall >=10 runs (Postgres)",
}
PROMETHEUS_DETECTORS = {"A", "C", "E"}


def ollama_clean() -> tuple[bool, str]:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:  # noqa: BLE001
        return True, f"ollama ps unavailable ({e}) — treated as clean"
    loaded = [ln for ln in out.splitlines()[1:] if ln.strip()]
    bad = [ln.split()[0] for ln in loaded if any(g in ln.lower() for g in _GEN_MODELS)]
    return (not bad), (f"generation model loaded: {bad}" if bad else "heat 0 (no generation model)")


def _resolve_db_url(db_url: str | None) -> str:
    add_grace_to_path()
    if db_url:
        return db_url
    from src.shared.config import GraceSettings  # type: ignore

    live = GraceSettings().database_url
    head, _, dbname = live.rpartition("/")
    dbname = dbname.split("?", 1)[0]
    return live if dbname.endswith("_test") else f"{head}/{dbname}_test"


def run_pipeline(db_url: str, signals: list[str] | None, modules: list[str] | None,
                 config: str | None) -> tuple[str | None, dict]:
    """Spawn the sanctioned CLI (D246) against db_url. Returns (run_id, raw_summary)."""
    argv = [sys.executable, "-m", "src.analytics.signal_pipeline", "run-all"]
    for s in signals or []:
        argv += ["--signal", s]
    for m in modules or []:
        argv += ["--ontology-module", m]
    if config:
        argv += ["--config", config]
    env = dict(os.environ, DATABASE_URL=db_url, PYTHONPATH=GRACE_ROOT)
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=GRACE_ROOT, env=env, timeout=300)
    summary = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and "run_id" in line:
            try:
                summary = json.loads(line)
            except Exception:  # noqa: BLE001
                pass
    if not summary:
        return None, {"error": "no run summary", "stderr": proc.stderr[-500:], "rc": proc.returncode}
    return summary.get("run_id"), summary


def read_signals(db_url: str, run_id: str) -> list[dict]:
    from sqlalchemy import create_engine, text

    eng = create_engine(db_url, pool_pre_ping=True)
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT signal_type, ontology_module, strength, evidence_snapshot
                FROM analytics_signals WHERE run_id = :rid
                ORDER BY signal_type, ontology_module
                """
            ),
            {"rid": run_id},
        ).all()
    out = []
    for st, mod, strength, ev in rows:
        ev = ev or {}
        out.append({
            "signal": st, "module": mod, "strength": round(float(strength), 3),
            "fired": float(strength) > 0.0,
            "trend": ev.get("trend"),
            "evidence": {k: ev.get(k) for k in
                         ("orphan_pairs", "total_pairs", "p_value", "entity_type")
                         if ev.get(k) is not None},
        })
    return out


def probe(db_url: str, signals: list[str] | None, modules: list[str] | None,
          config: str | None) -> dict:
    requested = signals or list("ABCDEF")
    run_id, summary = run_pipeline(db_url, signals, modules, config)
    report: dict = {
        "db": db_url.rsplit("/", 1)[-1].split("?")[0],
        "run_id": run_id,
        "requested_signals": requested,
        "summary": summary,
    }
    if not run_id:
        report["error"] = summary
        return report
    sigs = read_signals(db_url, run_id)
    report["signals"] = sigs
    fired = {s["signal"] for s in sigs if s["fired"]}
    quiet_records = {s["signal"] for s in sigs if not s["fired"]}
    produced = {s["signal"] for s in sigs}
    # substrate honesty: detectors that produced NO record at all
    no_record = [d for d in requested if d not in produced]
    substrate_report = []
    for d in no_record:
        kind = ("Prometheus — needs telemetry history; correctly no-op when absent"
                if d in PROMETHEUS_DETECTORS else
                "Postgres — no qualifying substrate in window (not a false-fire)")
        substrate_report.append({"signal": d, "no_op": True,
                                 "reads": SUBSTRATE[d], "interpretation": kind})
    report["fired"] = sorted(fired)
    report["quiet_with_record"] = sorted(quiet_records)
    report["no_op_substrate"] = substrate_report
    return report


def _print(rep: dict) -> None:
    print(f"SIGNAL PROBE — db={rep['db']} run={rep.get('run_id')}")
    if rep.get("error"):
        print(f"  ✗ pipeline error: {rep['error']}")
        return
    print(f"  requested: {rep['requested_signals']}")
    print("\n  per-signal records:")
    for s in rep.get("signals", []):
        flag = "🔥 FIRED" if s["fired"] else "· quiet"
        extra = f" trend={s['trend']}" if s.get("trend") else ""
        ev = f" {s['evidence']}" if s.get("evidence") else ""
        print(f"    [{flag}] {s['signal']} / {s['module']:<22} strength={s['strength']}{extra}{ev}")
    if rep.get("no_op_substrate"):
        print("\n  substrate-honest NO-OP (no record produced — NOT a false negative):")
        for n in rep["no_op_substrate"]:
            print(f"    · {n['signal']}: {n['interpretation']}")
            print(f"        reads: {n['reads']}")
    clean, msg = ollama_clean()
    print(f"\n  {msg}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--signal", action="append", choices=list("ABCDEF"),
                    help="restrict to these detectors (repeatable)")
    ap.add_argument("--module", action="append", help="restrict to these ontology modules")
    ap.add_argument("--db-url", default=None, help="target db (default grace_test sibling)")
    ap.add_argument("--config", default=None, help="signal_pipeline.yaml override path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    route_logs_to_stderr(quiet=True)

    db_url = _resolve_db_url(args.db_url)
    rep = probe(db_url, args.signal, args.module, args.config)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        _print(rep)


if __name__ == "__main__":
    main()
