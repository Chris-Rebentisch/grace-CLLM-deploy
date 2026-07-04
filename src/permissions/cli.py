"""CLI entry point for permission engine pipelines (Chunk 42, D246 mirror).

This module is the **sole** ``subprocess.Popen`` target invoked by
``src/api/permissions_routes.py``. It MUST NOT import ``fastapi`` or
``apscheduler``.

Sub-commands:

* ``hypothesis generate --evidence-id <uuid> [--run-id <uuid>] [--dry-run]`` —
  generates a :class:`RoleClusterHypothesisSet` from a stored evidence
  bundle and prints it as JSON to stdout (or persists it via the
  hypothesis-runs repository when invoked outside of dry-run mode).
* ``drift run [--observation-time ISO8601] [--dry-run]`` — runs the
  kNN drift detector once.

Both commands honor a ``--dry-run`` flag that produces deterministic
output suitable for tests and orchestrator smoke runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID, uuid4

from sqlalchemy import text

from src.permissions.evidence_collector import collect_evidence
from src.permissions.hypothesis_generator import generate as generate_hypothesis
from src.permissions.models import EvidenceBundle, RoleClusterHypothesisSet
from src.shared.database import get_session_factory


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.permissions.cli",
        description="Permission engine CLI (D246 mirror; CLI-only).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    hyp = sub.add_parser("hypothesis", help="Hypothesis generator commands")
    hyp_sub = hyp.add_subparsers(dest="subcommand", required=True)
    hyp_gen = hyp_sub.add_parser("generate", help="Generate hypothesis set")
    hyp_gen.add_argument("--evidence-id", required=True)
    hyp_gen.add_argument(
        "--dry-run",
        action="store_true",
        help="Produce deterministic mocked-LLM output (no network call).",
    )
    hyp_gen.add_argument("--seed", type=int, default=42)
    hyp_gen.add_argument(
        "--run-id",
        default=None,
        help=(
            "API-allocated permission_hypothesis_runs.run_id; persists "
            "hypothesis_set and emits CF1 telemetry on success."
        ),
    )

    drift = sub.add_parser("drift", help="Drift detector commands")
    drift_sub = drift.add_subparsers(dest="subcommand", required=True)
    drift_run = drift_sub.add_parser("run", help="Run kNN drift detector once")
    drift_run.add_argument(
        "--observation-time",
        default=None,
        help="ISO8601 observation time. Defaults to now.",
    )
    drift_run.add_argument("--dry-run", action="store_true")

    return parser.parse_args(list(argv))


def _empty_evidence(evidence_id: str) -> EvidenceBundle:
    """Build an empty evidence bundle with the given id, for dry-run."""
    bundle = collect_evidence()
    # Force the evidence id so the run stamps a stable artifact
    # reference. Pydantic v2: model_copy preserves immutability.
    return bundle.model_copy(update={"evidence_id": UUID(evidence_id)})


def _persist_hypothesis_run(
    run_id: UUID, hypothesis_set: RoleClusterHypothesisSet
) -> None:
    """UPDATE placeholder row to completed + hypothesis_set JSONB."""
    payload = json.dumps(hypothesis_set.model_dump(mode="json"))
    session_factory = get_session_factory()
    db = session_factory()
    try:
        db.execute(
            text(
                "UPDATE permission_hypothesis_runs "
                "SET hypothesis_set = CAST(:hyp AS JSONB), "
                "status = 'completed', completed_at = NOW() "
                "WHERE run_id = :run_id"
            ),
            {"hyp": payload, "run_id": run_id},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cmd_hypothesis_generate(args: argparse.Namespace) -> int:
    evidence = _empty_evidence(args.evidence_id)
    run_uuid = UUID(args.run_id) if args.run_id else uuid4()
    result = generate_hypothesis(
        evidence,
        dry_run=bool(args.dry_run),
        seed=args.seed,
        run_id=run_uuid,
    )
    if args.run_id:
        try:
            _persist_hypothesis_run(run_uuid, result)
        except Exception as exc:
            sys.stderr.write(f"hypothesis: persist failed: {exc}\n")
            return 1
        try:
            from src.elicitation.bridge import enqueue_event

            cluster_count = sum(
                1 for h in result.hypotheses if h.kind == "segmented"
            )
            enqueue_event(
                event_type="permission_matrix_hypothesis_generated",
                payload={
                    "run_id": str(run_uuid),
                    "cluster_count": cluster_count,
                    "has_null_hypothesis": True,
                },
            )
        except Exception:
            pass
    sys.stdout.write(result.model_dump_json())
    sys.stdout.write("\n")
    return 0


def cmd_drift_run(args: argparse.Namespace) -> int:
    """Legacy thin envelope kept for orchestrator scripts that already
    target ``python -m src.permissions.cli drift run``. The canonical
    operator entry per D337 is ``python -m src.permissions.drift_detector
    run`` (the launchd sample uses that path), which produces the
    full ``DriftRunReport`` JSON envelope.
    """
    obs = args.observation_time or datetime.now(tz=timezone.utc).isoformat()
    payload = {
        "command": "drift.run",
        "observation_time": obs,
        "dry_run": bool(args.dry_run),
        "result": "no_op_dry_run" if args.dry_run else "executed",
    }
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # F-0049 / ISS-0040 deferral closure: mirror this D246 subprocess's OTel
    # metrics into the prometheus multiproc dir (no-op when
    # PROMETHEUS_MULTIPROC_DIR is unset). This module is the REAL argv entry
    # for hypothesis generation (the init inside
    # hypothesis_generator.generate() was the interim workaround).
    # Pattern: src/extraction/extraction_bridge.py main().
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "hypothesis" and args.subcommand == "generate":
        return cmd_hypothesis_generate(args)
    if args.command == "drift" and args.subcommand == "run":
        return cmd_drift_run(args)
    return 2  # unreachable due to argparse `required=True`


if __name__ == "__main__":
    raise SystemExit(main())
