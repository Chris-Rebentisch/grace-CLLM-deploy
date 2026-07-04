#!/usr/bin/env python3
"""Seed deterministic SIGNAL COMBINATIONS directly into `analytics_signals` (the
correlation engine's D252 input) in a SANDBOX database, so the cross-module
correlation PATTERNS have substrate to fire on — the A4 fault-injection half of
grace-correlation-probe.

WHY direct analytics_signals seeding (not A3's seed→signal-pipeline chain): the
correlation engine reads ONLY `analytics_signals` + `signal_runs` + a small raw-
Prometheus allowlist (D252). The cleanest, most controllable substrate for testing
the CORRELATION layer is the signal table itself — one row per (signal, module,
strength). This decouples A4 from the signal pipeline's quirks and lets us inject
exact signal COMBINATIONS the patterns key on.

SAFETY (load-bearing): refuses any database whose name does not end in `_test`.
Seeded rows carry the `a4probe` marker (`config_hash='a4probe'`, evidence
`{"a4probe":true}`) so `--clean` removes exactly them. All combinations land under
ONE `signal_runs` row (status='success', now()) so the engine's "latest successful
run" read picks them all up together.

Combinations (each is a {module: [(signal, strength)]} fixture):
  RECALL (the right pattern MUST fire):
    drift      C=0.80 D=0.70 a4probe_drift     -> schema_drift_per_module (ontology)
    cqreg      F=0.90        a4probe_cqreg     -> cq_regression_pre_extraction (discovery)
    econflict  E=0.80 B=0.60 a4probe_econflict -> ontology_constraint_conflict (ontology, D535)
  PRECISION (must stay quiet — conjunction not satisfied / below floor):
    healthy    C=0.80        a4probe_healthy   -> schema_drift needs C AND D
    lowf       F=0.30        a4probe_lowf      -> cq_regression needs F>=0.5
    eonly      E=0.80        a4probe_eonly     -> ontology_constraint_conflict needs E AND B
    thin       A=0.55        a4probe_thin      -> single weak signal, no corroboration

NOT cleanly firable here (substrate-honest — Prometheus-gated, see design doc):
  extraction_quality_problem / graph_or_index_problem / relationship_gap_propagation
  read the raw-Prometheus allowlist (current-vs-14d-baseline sigma). No pushgateway
  on this box -> they correctly NO-OP even when their DB signal (A / B) is present.
  The probe asserts that honest no-op rather than faking a fire.

Usage:
  python3 seed_correlations.py --combination all      # seed every fixture (one run)
  python3 seed_correlations.py --combination drift,cqreg
  python3 seed_correlations.py --clean                # remove all a4probe rows
  python3 seed_correlations.py --db-url <url>         # override sandbox (must end _test)
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

MARKER = "a4probe"

# (signal_type, ontology_module, strength) fixtures, grouped by combination name.
COMBINATIONS: dict[str, list[tuple[str, str, float]]] = {
    "drift": [("C", f"{MARKER}_drift", 0.80), ("D", f"{MARKER}_drift", 0.70)],
    "cqreg": [("F", f"{MARKER}_cqreg", 0.90)],
    "econflict": [("E", f"{MARKER}_econflict", 0.80), ("B", f"{MARKER}_econflict", 0.60)],
    "healthy": [("C", f"{MARKER}_healthy", 0.80)],
    "lowf": [("F", f"{MARKER}_lowf", 0.30)],
    "eonly": [("E", f"{MARKER}_eonly", 0.80)],
    "thin": [("A", f"{MARKER}_thin", 0.55)],
    # NOT in the gate set (`all`). A+C co-elevated is covered by NO pattern
    # (even post-D535) — used to exercise the Claude-as-reasoner RICHNESS path
    # (Claude diagnoses a cross-signal root cause the engine still misses).
    "uncovered": [("A", f"{MARKER}_uncovered", 0.70), ("C", f"{MARKER}_uncovered", 0.65)],
}

# The deterministic gate fixtures (`all` resolves to these — `uncovered` is
# opt-in so the golden gate's named recall/precision checks stay clean).
GATE_FIXTURES = ["drift", "cqreg", "econflict", "healthy", "lowf", "eonly", "thin"]

# Expected deterministic outcome per fixture module — the probe/gate ground truth.
EXPECTED: dict[str, dict] = {
    f"{MARKER}_drift": {"pattern": "schema_drift_per_module", "root_cause": "ontology", "fires": True, "strength": 0.75},
    f"{MARKER}_cqreg": {"pattern": "cq_regression_pre_extraction", "root_cause": "discovery", "fires": True, "strength": 0.90},
    f"{MARKER}_econflict": {"pattern": "ontology_constraint_conflict", "root_cause": "ontology", "fires": True, "strength": 0.70},
    f"{MARKER}_healthy": {"pattern": None, "fires": False},
    f"{MARKER}_lowf": {"pattern": None, "fires": False},
    f"{MARKER}_eonly": {"pattern": None, "fires": False},
    f"{MARKER}_thin": {"pattern": None, "fires": False},
}


def _sandbox_engine(db_url: str | None):
    """Build an engine bound to the SANDBOX db. Refuses non-_test databases."""
    add_grace_to_path()
    from sqlalchemy import create_engine

    if not db_url:
        from src.shared.config import GraceSettings  # type: ignore

        live = GraceSettings().database_url
        head, _, dbname = live.rpartition("/")
        dbname = dbname.split("?", 1)[0]
        db_url = live if dbname.endswith("_test") else f"{head}/{dbname}_test"
    tail = db_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not tail.endswith("_test"):
        raise SystemExit(
            f"[seed_correlations] REFUSING to seed non-test database '{tail}'. "
            f"Seeding is sandbox-only (db name must end in '_test')."
        )
    return create_engine(db_url, pool_pre_ping=True), db_url


def _clean(conn) -> dict:
    from sqlalchemy import text

    n_sig = conn.execute(
        text("DELETE FROM analytics_signals WHERE evidence_snapshot->>'a4probe' = 'true'")
    ).rowcount
    n_run = conn.execute(
        text("DELETE FROM signal_runs WHERE config_hash = :m"), {"m": MARKER}
    ).rowcount
    return {"analytics_signals": n_sig, "signal_runs": n_run}


def _seed(conn, fixtures: list[tuple[str, str, float]]) -> dict:
    from sqlalchemy import text

    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    conn.execute(
        text(
            "INSERT INTO signal_runs (id, started_at, completed_at, status, "
            "triggered_by, config_hash) VALUES (:id,:s,:c,'success','cli',:m)"
        ),
        {"id": run_id, "s": now, "c": now, "m": MARKER},
    )
    evidence = json.dumps({"a4probe": True})
    for sig, module, strength in fixtures:
        conn.execute(
            text(
                "INSERT INTO analytics_signals (id, run_id, signal_type, "
                "ontology_module, strength, evidence_snapshot, detected_at) VALUES "
                "(gen_random_uuid(), :rid, :sig, :mod, :st, CAST(:ev AS jsonb), :d)"
            ),
            {"rid": run_id, "sig": sig, "mod": module, "st": strength,
             "ev": evidence, "d": now},
        )
    return {"run_id": run_id, "rows": len(fixtures)}


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--combination", default="all",
                    help="comma list of fixtures or 'all' (default all)")
    ap.add_argument("--clean", action="store_true", help="remove a4probe rows and exit")
    ap.add_argument("--db-url", default=None, help="sandbox URL override (must end _test)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    route_logs_to_stderr(quiet=True)
    engine, db_url = _sandbox_engine(args.db_url)

    with engine.begin() as conn:
        if args.clean:
            out = {"action": "clean", "db": db_url.rsplit("/", 1)[-1], **_clean(conn)}
        else:
            names = []
            for tok in args.combination.split(","):
                tok = tok.strip()
                if not tok:
                    continue
                if tok.lower() == "all":
                    names.extend(GATE_FIXTURES)
                else:
                    names.append(tok)
            unknown = [n for n in names if n not in COMBINATIONS]
            if unknown:
                raise SystemExit(f"[seed_correlations] unknown combination(s): {unknown}")
            fixtures: list[tuple[str, str, float]] = []
            for n in names:
                fixtures.extend(COMBINATIONS[n])
            seeded = _seed(conn, fixtures)
            out = {"action": "seed", "db": db_url.rsplit("/", 1)[-1],
                   "combinations": names, **seeded}

    if args.json:
        print(json.dumps(out, default=str))
    else:
        print(out)


if __name__ == "__main__":
    main()
