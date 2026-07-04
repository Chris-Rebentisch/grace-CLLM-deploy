#!/usr/bin/env python3
"""grace-correlation-probe — drive the deterministic correlation engine as a CLIENT
(D246: CLI-only, out-of-process) against a SANDBOX db and read back what it
diagnosed. Heat-free: the engine is LLM-free (grep get_provider
src/analytics/correlation_engine/ -> empty; PromQL/SQL + Mann-Kendall only).

The probe:
  1. clears the sandbox correlation OUTPUT tables (diagnostic_records,
     correlation_runs) — sandbox only, never the live grace GOLD corpus,
  2. spawns `python -m src.analytics.correlation_engine run-all` with
     DATABASE_URL pointed at the sandbox (the harness drives the CLI),
  3. reads back diagnostic_records as structured ground truth.

  python3 correlation_probe.py --json
  python3 correlation_probe.py --pattern ontology_constraint_conflict --json
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


def _resolve_db_url(override: str | None) -> str:
    """Return the SANDBOX db URL (the `_test` sibling). Refuses non-_test."""
    add_grace_to_path()
    from src.shared.config import GraceSettings  # type: ignore

    if override:
        db_url = override
    else:
        live = GraceSettings().database_url
        head, _, dbname = live.rpartition("/")
        dbname = dbname.split("?", 1)[0]
        db_url = live if dbname.endswith("_test") else f"{head}/{dbname}_test"
    tail = db_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not tail.endswith("_test"):
        raise SystemExit(
            f"[correlation_probe] refusing to run against '{tail}' (db name must end _test)."
        )
    return db_url


def _engine(db_url: str):
    from sqlalchemy import create_engine

    return create_engine(db_url, pool_pre_ping=True)


def _clear_outputs(db_url: str) -> None:
    """TRUNCATE the sandbox correlation output tables (safe — _test only)."""
    from sqlalchemy import text

    with _engine(db_url).begin() as conn:
        conn.execute(text("TRUNCATE diagnostic_records, correlation_runs"))


def run_engine(db_url: str, patterns: list[str] | None = None,
               dry_run: bool = False) -> dict:
    """Spawn the engine CLI with DATABASE_URL overridden to the sandbox."""
    root = add_grace_to_path()
    argv = [sys.executable, "-m", "src.analytics.correlation_engine", "run-all"]
    for p in patterns or []:
        argv += ["--pattern", p]
    if dry_run:
        argv.append("--dry-run")
    env = dict(os.environ, DATABASE_URL=db_url, PYTHONPATH=str(root))
    proc = subprocess.run(argv, capture_output=True, text=True, env=env,
                          cwd=str(root), timeout=180)
    summary = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
            except Exception:  # noqa: BLE001
                pass
    summary.setdefault("exit_code", proc.returncode)
    if proc.returncode not in (0, 1):
        summary["stderr_tail"] = proc.stderr[-400:]
    return summary


def read_diagnostics(db_url: str) -> list[dict]:
    """Return diagnostic_records as structured rows (the engine's output)."""
    from sqlalchemy import text

    with _engine(db_url).begin() as conn:
        rows = conn.execute(
            text(
                "SELECT pattern_name, ontology_module, suspected_root_cause_module, "
                "correlation_strength, contributing_signals, human_summary, "
                "evidence_snapshot "
                "FROM diagnostic_records ORDER BY pattern_name, ontology_module"
            )
        ).all()
    out = []
    for r in rows:
        evidence = dict(r[6] or {})
        out.append({
            "pattern": r[0],
            "module": r[1],
            "root_cause": r[2],
            # candidate_root_causes surfaces the engine's boundary cases (D535) so the
            # scorer can credit a reasoner who picks any defensible candidate.
            "candidate_root_causes": evidence.get("candidate_root_causes", [r[2]]),
            "strength": round(float(r[3]), 3),
            "contributing_signals": r[4],
            "summary": r[5],
        })
    return out


def probe(db_url: str, patterns: list[str] | None = None) -> dict:
    """Clear sandbox outputs, run the engine via CLI, read back diagnostics."""
    _clear_outputs(db_url)
    summary = run_engine(db_url, patterns=patterns)
    diagnostics = read_diagnostics(db_url)
    return {"summary": summary, "diagnostics": diagnostics,
            "n_diagnostics": len(diagnostics)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", action="append", default=None,
                    help="restrict to one or more patterns")
    ap.add_argument("--db-url", default=None,
                    help="SQLAlchemy URL of your sandbox (must end _test); "
                         "overrides DATABASE_URL. Defaults to the _test sibling of "
                         "the configured DB.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    add_grace_to_path()
    route_logs_to_stderr(quiet=True)
    db_url = _resolve_db_url(args.db_url)
    res = probe(db_url, patterns=args.pattern)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"engine: {res['summary']}")
        for d in res["diagnostics"]:
            print(f"  [{d['pattern']}] {d['module']} -> {d['root_cause']} "
                  f"({d['strength']})")


if __name__ == "__main__":
    main()
