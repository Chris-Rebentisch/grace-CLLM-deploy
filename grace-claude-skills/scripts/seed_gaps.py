#!/usr/bin/env python3
"""Seed deterministic ontology gaps into a SANDBOX database so the signal
detectors have substrate to fire on — the A3 "fault injection" half of
grace-signal-probe.

WHY this exists: on a quiet/fresh GrACE box all six detectors legitimately
NO-OP (Prometheus grace_* namespace empty; extraction_claims single-day +
ontology_module NULL; cq_test_runs empty). To test DETECTION FIDELITY (does
the right detector fire on a known gap, and stay quiet on a healthy module)
we must inject the gap first. We do that in the `grace_test` sibling — NEVER
the live `grace` GOLD corpus.

SAFETY (load-bearing): this script REFUSES to run against any database whose
name does not end in `_test`. The seeded rows carry markers so `--clean`
removes exactly them and nothing else (idempotent).

Seedable detectors (heat-free, Postgres-only):
  • B  co-occurrence-without-edge  — orphan entity pairs in a chunk (strength=orphans/total)
  • D  deprecation                 — Mann-Kendall DECREASING daily entity-type count (p<0.05)
  • F  CQ-driven gaps              — Mann-Kendall INCREASING cq_test_runs failure rate (p<0.05)

NOT cleanly seedable here (substrate-honest — see references/signal-probe-design.md §A/C/E):
  • A/C/E read Prometheus and compare a current window vs a 14-day baseline
    (sigma / rate). Producing a real fire needs timestamp-controlled TSDB
    history (a genuine baseline period + a genuine spike) — there is no
    pushgateway / remote-write ingress on this box, and a single synthetic
    sample cannot create the sigma separation. `--prometheus` reports this
    honestly rather than faking a fire.

Usage:
  python3 seed_gaps.py --detector all          # seed B + D + F into grace_test
  python3 seed_gaps.py --detector B            # just B
  python3 seed_gaps.py --clean                 # remove all a3probe_ marker rows
  python3 seed_gaps.py --prometheus            # report A/C/E substrate reality
  python3 seed_gaps.py --db-url <url>          # override sandbox URL (must end _test)
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402

MARKER = "a3probe"  # every seeded row carries this so --clean is exact

# ---- module / type names used by the fixtures (stable, greppable) ----
MOD_ORPHAN = f"{MARKER}_orphan"      # B: 3 entities, no edge -> strength 1.0 (RECALL)
MOD_HEALTHY = f"{MARKER}_healthy"    # B: 2 entities + connecting edge -> 0.0 (PRECISION)
MOD_DEPRECATE = f"{MARKER}_deprecate"  # D: decreasing daily count (RECALL)
MOD_STABLE = f"{MARKER}_stable"      # D: flat daily count -> no fire (PRECISION)


def _sandbox_engine(db_url: str | None):
    """Build an engine bound to the SANDBOX db. Refuses non-_test databases."""
    add_grace_to_path()
    from sqlalchemy import create_engine

    if not db_url:
        from src.shared.config import GraceSettings  # type: ignore

        live = GraceSettings().database_url  # e.g. postgresql+psycopg2://u@h/grace
        # swap the trailing db name -> <db>_test
        head, _, dbname = live.rpartition("/")
        dbname = dbname.split("?", 1)[0]
        if dbname.endswith("_test"):
            db_url = live
        else:
            db_url = f"{head}/{dbname}_test"
    tail = db_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not tail.endswith("_test"):
        raise SystemExit(
            f"[seed_gaps] REFUSING to seed a non-test database '{tail}'. "
            f"Seeding is sandbox-only (db name must end in '_test')."
        )
    return create_engine(db_url, pool_pre_ping=True), db_url


def _clean(conn) -> dict:
    from sqlalchemy import text

    n_claims = conn.execute(
        text("DELETE FROM extraction_claims WHERE source_document_id LIKE :m"),
        {"m": f"{MARKER}_%"},
    ).rowcount
    n_cq = conn.execute(
        text("DELETE FROM cq_test_runs WHERE model = :m"), {"m": MARKER}
    ).rowcount
    n_sig = conn.execute(
        text("DELETE FROM analytics_signals WHERE ontology_module LIKE :m"),
        {"m": f"{MARKER}_%"},
    ).rowcount
    # CQ apply-fixture (active version + competency_questions) for co-signal 4.
    n_cqq = conn.execute(
        text("DELETE FROM competency_questions WHERE template_id = :m"), {"m": MARKER}
    ).rowcount
    # ontology_versions is APPEND-ONLY (prevent_ontology_version_mutation trigger) —
    # DELETE is forbidden; deactivate the fixture version instead (UPDATE is_active
    # is permitted). Inactive fixture versions accumulate harmlessly in the sandbox.
    n_ver = conn.execute(
        text("UPDATE ontology_versions SET is_active=false WHERE hash_chain = :m AND is_active=true"),
        {"m": f"{MARKER}_apply"},
    ).rowcount
    return {"extraction_claims": n_claims, "cq_test_runs": n_cq,
            "analytics_signals": n_sig, "competency_questions": n_cqq,
            "ontology_versions_deactivated": n_ver}


def _entity_claim(module: str, chunk: str, name: str, etype: str, when: datetime) -> dict:
    return {
        "claim_id": str(uuid.uuid4()),
        "extraction_unit_id": f"{MARKER}_eu_{uuid.uuid4().hex[:8]}",
        "entity_type": etype,
        "relationship_type": None,
        "subject_name": name,
        "predicate": "instance_of",
        "object_name": None,
        "status": "promoted",
        "decision_source": "llm",
        "source_document_id": f"{MARKER}_doc",
        "source_chunk_id": chunk,
        "ontology_module": module,
        "verdict": "SUPPORTED",
        "created_at": when,
    }


def _rel_claim(module: str, chunk: str, subj: str, rel: str, obj: str, when: datetime) -> dict:
    return {
        "claim_id": str(uuid.uuid4()),
        "extraction_unit_id": f"{MARKER}_eu_{uuid.uuid4().hex[:8]}",
        "entity_type": None,
        "relationship_type": rel,
        "subject_name": subj,
        "predicate": rel,
        "object_name": obj,
        "status": "promoted",
        "decision_source": "llm",
        "source_document_id": f"{MARKER}_doc",
        "source_chunk_id": chunk,
        "ontology_module": module,
        "verdict": "SUPPORTED",
        "created_at": when,
    }


_CLAIM_INSERT = """
INSERT INTO extraction_claims
  (claim_id, extraction_unit_id, entity_type, relationship_type, subject_name,
   predicate, object_name, status, decision_source, source_document_id,
   source_chunk_id, ontology_module, verdict, created_at)
VALUES
  (:claim_id, :extraction_unit_id, :entity_type, :relationship_type, :subject_name,
   :predicate, :object_name, :status, :decision_source, :source_document_id,
   :source_chunk_id, :ontology_module, :verdict, :created_at)
"""


def _seed_b(conn) -> dict:
    """B: orphan module (3 entities, 0 edges -> 1.0) + healthy module (2 + edge -> 0.0)."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    rows = [
        # ORPHAN: 3 entities co-occur in one chunk, NO relationship -> 3 orphan pairs
        _entity_claim(MOD_ORPHAN, f"{MARKER}_chunk_orphan", "Alice Tan", "Person", now),
        _entity_claim(MOD_ORPHAN, f"{MARKER}_chunk_orphan", "Acme Holdings", "Company", now),
        _entity_claim(MOD_ORPHAN, f"{MARKER}_chunk_orphan", "Board of Trustees", "Body", now),
        # HEALTHY: 2 entities + a relationship claim connecting them -> 0 orphan pairs
        _entity_claim(MOD_HEALTHY, f"{MARKER}_chunk_healthy", "Bob Lee", "Person", now),
        _entity_claim(MOD_HEALTHY, f"{MARKER}_chunk_healthy", "Beta LLC", "Company", now),
        _rel_claim(MOD_HEALTHY, f"{MARKER}_chunk_healthy", "Bob Lee", "works_for", "Beta LLC", now),
    ]
    conn.execute(text(_CLAIM_INSERT), rows)
    return {"detector": "B", "modules": {MOD_ORPHAN: "expect 1.0", MOD_HEALTHY: "expect 0.0"},
            "rows": len(rows)}


def _seed_d(conn, n_days: int = 12) -> dict:
    """D: decreasing daily count for one type (RECALL) + flat for another (PRECISION).

    Mann-Kendall needs >= mann_kendall_min_points (10) distinct days. We lay
    down n_days of daily claims: LegacyDeed monotonically DECREASING (fires),
    StableType FLAT (no trend, quiet). Distinct chunk per (type, day) so these
    never form Signal-B co-occurrence pairs.
    """
    from sqlalchemy import text

    now = datetime.now(UTC)
    rows: list[dict] = []
    for d in range(n_days):
        day = now - timedelta(days=(n_days - d))  # oldest first
        # DECREASING: counts go high -> low as time advances (deprecation)
        dec_count = max(1, (n_days - d) + 2)  # day0~14 ... last~3
        for i in range(dec_count):
            rows.append(_entity_claim(
                MOD_DEPRECATE, f"{MARKER}_dep_d{d}_{i}", "LegacyDeed", "LegacyDeed", day))
        # FLAT control: constant 5/day -> no monotone trend
        for i in range(5):
            rows.append(_entity_claim(
                MOD_STABLE, f"{MARKER}_stb_d{d}_{i}", "StableType", "StableType", day))
    conn.execute(text(_CLAIM_INSERT), rows)
    return {"detector": "D", "modules": {MOD_DEPRECATE: "expect fire (decreasing)",
                                         MOD_STABLE: "expect quiet (flat)"},
            "days": n_days, "rows": len(rows)}


def _ensure_sentinel_version(conn) -> str:
    """F's cq_test_runs.schema_version_id FK -> ontology_versions. Ensure one exists."""
    from sqlalchemy import text

    existing = conn.execute(
        text("SELECT id FROM ontology_versions WHERE source = :m LIMIT 1"), {"m": MARKER}
    ).first()
    if existing:
        return str(existing[0])
    vid = str(uuid.uuid4())
    conn.execute(
        text(
            """
            INSERT INTO ontology_versions
              (id, version_number, created_at, schema_json, schema_modules,
               hash_chain, source, is_active)
            VALUES
              (:id, :vn, :ts, :sj, :sm, :hc, :src, false)
            """
        ),
        {"id": vid, "vn": 90000, "ts": datetime.now(UTC),
         "sj": json.dumps({"types": []}), "sm": json.dumps({}),
         "hc": f"{MARKER}_sentinel", "src": MARKER},
    )
    return vid


def _seed_f(conn, n_runs: int = 12) -> dict:
    """F: n_runs completed cq_test_runs with INCREASING failure rate (RECALL)."""
    from sqlalchemy import text

    vid = _ensure_sentinel_version(conn)
    now = datetime.now(UTC)
    total = 20
    rows = []
    for i in range(n_runs):
        failing = i + 1  # 1,2,3,... -> rising failure rate
        passing = total - failing
        rows.append({
            "id": str(uuid.uuid4()),
            "created_at": now - timedelta(hours=(n_runs - i)),
            "schema_version_id": vid,
            "is_proposed_schema": False,
            "total_cqs": total,
            "passing": passing,
            "failing": failing,
            "out_of_scope": 0,
            "errors": 0,
            "pass_rate": passing / total,
            "status": "completed",
            "concurrency": 1,
            "duration_ms": 1000,
            "model": MARKER,
            "results_json": json.dumps([
                {"cq_id": f"{MARKER}_cq{failing}", "cq_text": "Sample failing CQ",
                 "result": "fail"}
            ]),
        })
    conn.execute(
        text(
            """
            INSERT INTO cq_test_runs
              (id, created_at, schema_version_id, is_proposed_schema, total_cqs,
               passing, failing, out_of_scope, errors, pass_rate, status,
               concurrency, duration_ms, model, results_json)
            VALUES
              (:id, :created_at, :schema_version_id, :is_proposed_schema, :total_cqs,
               :passing, :failing, :out_of_scope, :errors, :pass_rate, :status,
               :concurrency, :duration_ms, :model, CAST(:results_json AS jsonb))
            """
        ),
        rows,
    )
    return {"detector": "F", "module": "__global__", "runs": n_runs,
            "expect": "fire (increasing failure rate)"}


def _seed_cq_fixture(conn) -> dict:
    """Co-signal-4 substrate: an active ontology_version (dict-shape schema the
    change_executor mutate expects) + ACCEPTED competency_questions for the CQ
    non-regression gate to run on qwen2.5:7b. Valid enums (status ACCEPTED,
    cq_type RELATIONSHIP, source HUMAN_AUTHORED, version source manual).
    """
    from sqlalchemy import text

    # exactly one active version: deactivate others, (re)insert the fixture
    conn.execute(text("UPDATE ontology_versions SET is_active=false WHERE is_active=true"))
    schema = {
        "entity_types": {
            "Party": {"type": "object", "description": "A contracting party", "properties": {}},
            "Agreement": {"type": "object", "description": "A legal agreement", "properties": {}},
        },
        "relationships": {},
    }
    vid = str(uuid.uuid4())
    # ontology_versions is append-only (old fixture versions can't be deleted) and
    # version_number is UNIQUE — compute the next free number so re-seeding never collides.
    next_vn = conn.execute(
        text("SELECT COALESCE(MAX(version_number), 99000) + 1 FROM ontology_versions")
    ).scalar()
    conn.execute(
        text(
            """
            INSERT INTO ontology_versions
              (id, version_number, created_at, schema_json, schema_modules,
               hash_chain, source, is_active)
            VALUES (:id, :vn, :ts, CAST(:sj AS jsonb), CAST(:sm AS jsonb), :hc, 'manual', true)
            """
        ),
        {"id": vid, "vn": next_vn, "ts": datetime.now(UTC),
         "sj": json.dumps(schema), "sm": json.dumps({}), "hc": f"{MARKER}_apply"},
    )
    cqs = [
        "Which parties are involved in an agreement?",
        "What agreements exist between two parties?",
    ]
    for txt in cqs:
        conn.execute(
            text(
                """
                INSERT INTO competency_questions
                  (id, canonical_text, cq_type, source, status, template_id, created_at, updated_at, domain)
                VALUES (:id, :t, 'RELATIONSHIP', 'HUMAN_AUTHORED', 'ACCEPTED', :m, :ts, :ts, 'legal')
                """
            ),
            {"id": str(uuid.uuid4()), "t": txt, "m": MARKER, "ts": datetime.now(UTC)},
        )
    return {"detector": "cq-fixture", "active_version": vid, "cqs": len(cqs),
            "schema_types": list(schema["entity_types"].keys())}


def _prometheus_reality() -> dict:
    """Substrate-honest report for A/C/E (Prometheus detectors)."""
    import urllib.request

    out = {"detectors": ["A", "C", "E"], "seedable": False}
    try:
        with urllib.request.urlopen(
            "http://localhost:9090/api/v1/query?query=count(%7B__name__%3D~%22grace_.%2A%22%7D)",
            timeout=5,
        ) as r:
            data = json.loads(r.read())
        res = data.get("data", {}).get("result", [])
        out["grace_series_in_prometheus"] = int(res[0]["value"][1]) if res else 0
    except Exception as e:  # noqa: BLE001
        out["grace_series_in_prometheus"] = f"query failed: {e}"
    # ingress check
    for name, url in (("pushgateway", "http://localhost:9091/metrics"),):
        try:
            urllib.request.urlopen(url, timeout=3)
            out[f"{name}_available"] = True
        except Exception:  # noqa: BLE001
            out[f"{name}_available"] = False
    out["note"] = (
        "A/C/E compare a current window vs a 14-day Prometheus baseline (sigma/rate). "
        "Firing requires real telemetry history (a genuine baseline + a genuine spike) "
        "or timestamp-controlled TSDB backfill. No pushgateway/remote-write ingress on "
        "this box, so a single synthetic sample cannot create the sigma separation. "
        "The probe verifies A/C/E RUN and correctly NO-OP on absent substrate; firing-"
        "validation for A/C/E is a documented follow-on (TSDB backfill harness)."
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--detector", choices=["B", "D", "F", "all"], default="all")
    ap.add_argument("--cq-fixture", action="store_true",
                    help="seed the active-version + ACCEPTED CQ fixture for the apply CQ-gate (co-signal 4)")
    ap.add_argument("--clean", action="store_true", help="remove all a3probe_ marker rows")
    ap.add_argument("--prometheus", action="store_true",
                    help="report A/C/E substrate reality (no seeding)")
    ap.add_argument("--db-url", default=None, help="sandbox DB url (must end _test)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    route_logs_to_stderr(quiet=True)

    if args.prometheus:
        rep = _prometheus_reality()
        print(json.dumps(rep, indent=2) if args.json else json.dumps(rep, indent=2))
        return

    engine, url = _sandbox_engine(args.db_url)
    report: dict = {"db": url.rsplit("/", 1)[-1]}
    with engine.begin() as conn:
        # always clean our markers first (idempotent)
        report["cleaned"] = _clean(conn)
        if not args.clean:
            if args.cq_fixture:
                report["cq_fixture"] = _seed_cq_fixture(conn)
            else:
                if args.detector in ("B", "all"):
                    report["B"] = _seed_b(conn)
                if args.detector in ("D", "all"):
                    report["D"] = _seed_d(conn)
                if args.detector in ("F", "all"):
                    report["F"] = _seed_f(conn)
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
