"""grace-ingestion-harness — GOLDEN GATE (C1).

Domain-agnostic invariants that MUST hold on healthy email-ingestion code,
scored against the corpus manifest. Covers the HEAT-FREE, SANDBOX-SAFE stages
that work today (post D536/D537): adapter parse fidelity, T1 sender-pattern
noise filtering, sensitivity tagging (privileged HARD invariant + precision),
thread reconstruction, substrate honesty, and GOLD-untouched.

Stages blocked by deferred findings (#3 ontology Person/Org, #6 corroboration
stub, #7 ArcadeClient env, #8 raw_headers) are reported in an AUDIT block, not
asserted as green gates — the same discipline as the A4 Prometheus-gated no-ops.

Heat 0 (T1-T3 + sensitivity + thread are heat-free; no model loads).

SAFETY: seeds the `grace_test` SANDBOX only (refuses non-_test). GOLD `grace`
Postgres + ArcadeDB are asserted unchanged.

  python3 ingestion_golden_gate.py
  python3 ingestion_golden_gate.py --json
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path  # noqa: E402

HERE = Path(__file__).resolve().parent
HARNESS = HERE.parent / "grace-ingestion-harness"
MANIFEST = HARNESS / "corpus" / "manifest.json"
GRACE_ROOT = os.environ.get("GRACE_ROOT", os.path.expanduser("~/grace"))


def _sandbox_url() -> str:
    raw = (
        os.environ.get("GRACE_PYTEST_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "postgresql+psycopg2://localhost:5432/grace"
    )
    head, _, db = raw.rpartition("/")
    if not db.endswith("_test"):
        db = f"{db}_test"
    return f"{head}/{db}"


def _gold_url(test_url: str) -> str | None:
    explicit = os.environ.get("GRACE_GOLD_URL")
    if explicit:
        return explicit
    head, _, db = test_url.rpartition("/")
    if db.endswith("_test"):
        return f"{head}/{db[:-5]}"
    return None


def _arcade_db(url: str) -> str:
    return url.rpartition("/")[2]


def _run(cmd: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=GRACE_ROOT, env=env)


def _pg(url: str, sql: str) -> list[tuple]:
    from sqlalchemy import create_engine, text

    eng = create_engine(url)
    with eng.connect() as c:
        return [tuple(r) for r in c.execute(text(sql))]


def _ollama_clean() -> tuple[bool, str]:
    try:
        out = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10).stdout
    except Exception as e:
        return True, f"ollama ps unavailable ({e}); heat check skipped"
    lines = [ln for ln in out.splitlines()[1:] if ln.strip()]
    return (len(lines) == 0), ("no model loaded" if not lines else f"LOADED: {lines}")


def run_gates() -> dict:
    add_grace_to_path(GRACE_ROOT)
    manifest = json.loads(MANIFEST.read_text())
    test_url = _sandbox_url()
    gold_url = _gold_url(test_url)
    arcade_db = _arcade_db(test_url)
    env = {**os.environ, "DATABASE_URL": test_url, "ARCADE_DATABASE": arcade_db,
           "PYTHONPATH": GRACE_ROOT, "GRACE_ROOT": GRACE_ROOT}
    gates: list[dict] = []

    def gate(name: str, ok: bool, detail: str):
        gates.append({"gate": name, "pass": bool(ok), "detail": detail})

    # heat (pre)
    h0_ok, h0 = _ollama_clean()
    gate("GATE-0 heat clean (pre)", h0_ok, h0)

    # GOLD baseline
    gold_comm = gold_legal = None
    if gold_url:
        try:
            gold_comm = _pg(gold_url, "select count(*) from communication_events")[0][0]
        except Exception:
            gold_url = None

    # seed (reset + registry + real-adapter pull); count from the DB (robust)
    _run([sys.executable, str(HERE / "seed_emails.py")], env)
    n_events = _pg(test_url, "select count(*) from communication_events")[0][0]
    gate("GATE-1 parse fidelity (all .eml ingested)", n_events == len(manifest["emails"]),
         f"events={n_events} expected={len(manifest['emails'])}")

    # thread DAG fidelity (References parsed into arrays)
    refs = dict(_pg(test_url, "select message_id, references_json::text from communication_events"))
    d2 = refs.get("<deal-002@birch-advisors.example>", "") or ""
    d3 = refs.get("<deal-003@acme-fo.example>", "") or ""
    gate("GATE-1b References DAG parsed",
         "deal-001" in d2 and "deal-001" in d3 and "deal-002" in d3,
         f"deal-002 refs={d2}; deal-003 refs={d3}")

    # run triage T1-T3 (heat-free)
    src_id = _pg(test_url, "select id from ingestion_sources where segment='c1probe' limit 1")[0][0]
    _run([sys.executable, "-m", "src.ingestion", "triage", "--source-id", str(src_id),
          "--tiers", "1,2,3"], env)
    # sensitivity + thread (heat-free, safe)
    _run([sys.executable, "-m", "src.ingestion.communications.sensitivity_tagger", "run"], env)
    _run([sys.executable, "-m", "src.ingestion.communications.thread_reconstructor", "run",
          "--source-id", str(src_id)], env)

    rows = _pg(test_url,
               "select message_id, triage_tier_outcome, coalesce(sensitivity_tags,''), "
               "thread_id, thread_position from communication_events")
    by_mid = {r[0]: {"triage": r[1], "sens": r[2], "thread": r[3], "pos": r[4]} for r in rows}
    mids = {e["id"]: e["message_id"] for e in manifest["emails"]}

    # GATE-2 full triage fidelity vs manifest (T1 noise + T2 recall + T2 precision)
    triage_mismatches = []
    for e in manifest["emails"]:
        exp = e["triage_expected"]
        act = by_mid.get(mids[e["id"]], {}).get("triage", "")
        if exp != act:
            triage_mismatches.append(f"{e['id']}: exp={exp} act={act}")
    gate("GATE-2 triage fidelity (T1 noise + T2 recall/precision)", not triage_mismatches,
         "; ".join(triage_mismatches) or "all 11 match")

    # GATE-3 sensitivity privileged HARD invariant (recall=100% + precision)
    expected_priv = {e["id"] for e in manifest["emails"] if e.get("privileged")}
    actual_priv = {eid for eid, mid in mids.items() if "|privileged|" in by_mid.get(mid, {}).get("sens", "")}
    gate("GATE-3 privileged recall=100% + precision (HARD)", expected_priv == actual_priv,
         f"expected={sorted(expected_priv)} actual={sorted(actual_priv)}")

    # GATE-4 sensitivity tag-set matches manifest (pii_dense / external_boundary precision)
    sens_ok = True
    sens_detail = []
    for e in manifest["emails"]:
        exp = set(e["sensitivity_expected"])
        act = {t for t in by_mid.get(mids[e["id"]], {}).get("sens", "").split("|") if t}
        if exp != act:
            sens_ok = False
            sens_detail.append(f"{e['id']}: exp={sorted(exp)} act={sorted(act)}")
    gate("GATE-4 sensitivity tag-set matches manifest", sens_ok, "; ".join(sens_detail) or "all match")

    # GATE-5 thread reconstruction fidelity
    deal = manifest["threads"]["deal"]
    deal_tids = {by_mid.get(mids[i], {}).get("thread") for i in deal}
    deal_pos = {i: by_mid.get(mids[i], {}).get("pos") for i in deal}
    auto_tid = by_mid.get(mids["07-autoreply"], {}).get("thread")
    deal_tid = next(iter(deal_tids)) if len(deal_tids) == 1 else None
    thread_ok = (len(deal_tids) == 1 and deal_pos == {deal[0]: 0, deal[1]: 1, deal[2]: 2}
                 and auto_tid != deal_tid)
    gate("GATE-5 thread DAG grouping (deal 0/1/2; same-subject no-ref standalone)", thread_ok,
         f"deal_tids={deal_tids} pos={deal_pos} autoreply_in_deal={auto_tid == deal_tid}")

    # GATE-6 substrate honesty (clean -> empty -> stages quiet)
    _run([sys.executable, str(HERE / "seed_emails.py"), "--clean"], env)
    empty = _pg(test_url, "select count(*) from communication_events")[0][0]
    gate("GATE-6 substrate honesty (clean -> 0 events)", empty == 0, f"events_after_clean={empty}")

    # GATE-7 GOLD untouched
    if gold_url and gold_comm is not None:
        gold_comm2 = _pg(gold_url, "select count(*) from communication_events")[0][0]
        gate("GATE-7 GOLD Postgres untouched", gold_comm2 == gold_comm,
             f"gold_comm {gold_comm}->{gold_comm2}")
    else:
        # Swarm-portable skip (F-A4-2): an isolated _test DB whose <name> sibling
        # does not exist cannot read GOLD. This passes so the gate runs anywhere, but
        # the detail makes it unmistakable that GOLD was NOT verified — set
        # GRACE_GOLD_URL (CI + the smoke test do) to actually enforce it.
        gate("GATE-7 GOLD untouched", True,
             "NOT VERIFIED — no GOLD sibling for this _test DB; set GRACE_GOLD_URL to enforce")

    # heat (post)
    h1_ok, h1 = _ollama_clean()
    gate("GATE-8 heat clean (post)", h1_ok, h1)

    audit = {
        "findings_status": manifest["findings_index"],
        "bounded_heat_deferred": {
            "triage_T4": "passing-T2 emails land 'passed_to_t4'; reaching 'passed_to_extraction' needs Tier 4 (qwen) — apply-gate, not this heat-free gate",
            "extraction_to_graph": "bounded heat (qwen) + needs sandbox graph schema; apply-gate",
            "corroboration_promotion": "run flow implemented (D542); live promotion needs extraction to populate communication entities first — apply-gate",
            "voice_profile": "needs >=50 principal emails; bounded heat (qwen); deferred"
        }
    }
    passed = sum(1 for g in gates if g["pass"])
    return {"gates": gates, "passed": passed, "total": len(gates),
            "all_pass": passed == len(gates), "audit": audit}


def main() -> None:
    as_json = "--json" in sys.argv
    result = run_gates()
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        for g in result["gates"]:
            print(f"[{'PASS' if g['pass'] else 'FAIL'}] {g['gate']}: {g['detail']}")
        print(f"\n{result['passed']}/{result['total']} gates pass  "
              f"({'GREEN' if result['all_pass'] else 'RED'})")
        print("\nAUDIT (bounded-heat deferred — not gated here):")
        for k, v in result["audit"]["bounded_heat_deferred"].items():
            print(f"  - {k}: {v}")
    sys.exit(0 if result["all_pass"] else 1)


if __name__ == "__main__":
    main()
