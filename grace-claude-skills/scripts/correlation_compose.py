#!/usr/bin/env python3
"""grace-correlation-probe — COMPOSE the correlation INPUT bundle for the
Claude-as-correlation-reasoner harness (A4 goal 2). Read-only: pulls exactly what
the deterministic engine reads (D252 — `analytics_signals` under the latest
successful `signal_runs`, grouped by module, with each signal's evidence) and emits
a domain-agnostic context block Claude reasons over IN-LOOP to produce a root-cause
diagnosis. Heat-free (no model touched here).

Domain-agnostic by construction: the signal legend (A–F meanings) comes from the
detector docstrings, not from any domain vocabulary. Modules/strengths are
discovered at runtime.

  python3 correlation_compose.py --json          # bundle for the reasoner + scorer
  python3 correlation_compose.py                 # human-readable context block
  python3 correlation_compose.py --include-engine # also attach the engine's diagnoses
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import add_grace_to_path, route_logs_to_stderr  # noqa: E402
import correlation_probe as cp  # noqa: E402

# Signal vocabulary — sourced from the signal-pipeline detector docstrings
# (Chunk 32, D240). Domain-NEUTRAL: these describe GrACE's own health signals.
SIGNAL_LEGEND = {
    "A": "extraction confidence regression (suspected module: extraction)",
    "B": "co-occurrence-without-edge / missing relationship (suspected: extraction)",
    "C": "ontology type drift (suspected: ontology)",
    "D": "type deprecation (suspected: ontology)",
    "E": "domain/range constraint violation (suspected: ontology vs extraction)",
    "F": "CQ-driven coverage gap (suspected: discovery)",
}


def pull_bundle(db_url: str) -> dict:
    """Return {modules: {module: [{signal, strength, evidence}]}} for the latest run."""
    from sqlalchemy import text

    with cp._engine(db_url).begin() as conn:
        rows = conn.execute(
            text(
                """
                WITH latest AS (
                    SELECT id FROM signal_runs WHERE status='success'
                    ORDER BY started_at DESC LIMIT 1
                )
                SELECT s.signal_type, s.ontology_module, s.strength, s.evidence_snapshot
                FROM analytics_signals s JOIN latest l ON s.run_id = l.id
                ORDER BY s.ontology_module, s.signal_type
                """
            )
        ).all()
    modules: dict[str, list[dict]] = {}
    for sig, module, strength, evidence in rows:
        modules.setdefault(module, []).append(
            {"signal": str(sig), "strength": round(float(strength), 3),
             "evidence": dict(evidence or {})}
        )
    return {"modules": modules}


def render_context(bundle: dict) -> str:
    """Render a plain-text context block — the fact set Claude reasons over."""
    lines = ["GrACE self-monitoring signals (latest run), grouped by ontology module.",
             "Signal legend:"]
    for k, v in SIGNAL_LEGEND.items():
        lines.append(f"  {k} = {v}")
    lines.append("")
    lines.append("Observed signals:")
    for module, sigs in sorted(bundle["modules"].items()):
        parts = ", ".join(
            f"Signal {s['signal']} strength {s['strength']:.2f}" for s in sigs
        )
        lines.append(f"  module '{module}': {parts}")
    lines.append("")
    lines.append(
        "Task: correlate signals into a cross-module root-cause diagnosis per module. "
        "Report root cause (one of: extraction, retrieval, graph, ontology, discovery) "
        "and a confidence BAND (low/medium/high) — never a numeric score. "
        "ABSTAIN only on a WEAK lone signal (strength below ~0.5) or a genuinely "
        "uncorrelated set; a SINGLE STRONG signal (>= ~0.5) is itself a diagnosable "
        "finding — diagnose it, do not abstain. When two signals co-occur in one module "
        "but point at different modules (e.g. E = 'ontology vs extraction'), the root "
        "cause sits on that boundary — name the most actionable side and say so. "
        "Cite the bare signal letters you used (e.g. C, D) and phrase each rationale in "
        "this legend's vocabulary so it stays grounded in the observed signals above.")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db-url", default=None,
                    help="SQLAlchemy URL of your sandbox (must end _test); "
                         "overrides DATABASE_URL. Defaults to the _test sibling.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--include-engine", action="store_true",
                    help="attach the deterministic engine's diagnoses (consistency ref)")
    args = ap.parse_args()

    add_grace_to_path()
    route_logs_to_stderr(quiet=True)
    db_url = cp._resolve_db_url(args.db_url)
    bundle = pull_bundle(db_url)
    context = render_context(bundle)

    out = {"context": context, "bundle": bundle, "signal_legend": SIGNAL_LEGEND}
    if args.include_engine:
        out["engine_diagnoses"] = cp.probe(db_url)["diagnostics"]

    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(context)
        if args.include_engine:
            print("\n--- deterministic engine diagnoses (consistency reference) ---")
            for d in out["engine_diagnoses"]:
                print(f"  [{d['pattern']}] {d['module']} -> {d['root_cause']} ({d['strength']})")


if __name__ == "__main__":
    main()
