"""kNN-over-centroids drift detector (Chunk 42, CP9, D337).

For each candidate Person, the drift detector compares the person's
feature vector against the centroid of every role-cluster in the active
``PermissionMatrix`` and classifies the closest match into one of three
bands per D337:

* ``high`` (similarity ≥ ``auto_assign_threshold``, default ``0.85``):
  auto-assign + audit row + ``permission_matrix_auto_assigned``
  telemetry.
* ``medium`` (``queue_with_guess_threshold`` ≤ similarity <
  ``auto_assign_threshold``, default ``0.60``): queue with the
  best-guess cluster pre-filled.
* ``low`` (similarity < ``queue_with_guess_threshold``): queue blind
  (``proposed_cluster_id`` is ``None``).

Similarity is cosine over the centroid space. A "centroid" is the
mean feature vector of a cluster's current members. When the active
matrix has no clusters, the detector emits one ``low``-band
classification per candidate (deny-bias on undefined topology).

CLI invocation (D246; sole sanctioned entry point):

    python -m src.permissions.drift_detector run \
        [--observation-time ISO8601] [--dry-run]

The module MUST NOT import ``fastapi`` or ``apscheduler``. Persistence
is performed via a SQLAlchemy session passed in by the CLI; tests
inject a stub session.

Bands are surfaced as label strings — the DOM never sees raw similarity
numerics (D120/D217 hold).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence
from uuid import UUID, uuid4

import structlog

_log = structlog.get_logger(__name__)

from src.permissions.models import (
    DriftBand,
    DriftClassification,
    PermissionMatrix,
)


# ----- Defaults / config -------------------------------------------


DEFAULT_AUTO_ASSIGN_THRESHOLD: float = 0.85
DEFAULT_QUEUE_WITH_GUESS_THRESHOLD: float = 0.60


@dataclass(frozen=True)
class DriftConfig:
    """Operator-tunable thresholds for ``classify``.

    Loaded from ``config/permissions.yaml`` by the CLI; tests construct
    instances directly.
    """

    auto_assign_threshold: float = DEFAULT_AUTO_ASSIGN_THRESHOLD
    queue_with_guess_threshold: float = DEFAULT_QUEUE_WITH_GUESS_THRESHOLD

    def __post_init__(self) -> None:
        if not 0.0 <= self.queue_with_guess_threshold <= 1.0:
            raise ValueError(
                "queue_with_guess_threshold must be in [0, 1]"
            )
        if not 0.0 <= self.auto_assign_threshold <= 1.0:
            raise ValueError("auto_assign_threshold must be in [0, 1]")
        if self.queue_with_guess_threshold > self.auto_assign_threshold:
            raise ValueError(
                "queue_with_guess_threshold must not exceed "
                "auto_assign_threshold"
            )


# ----- Inputs -------------------------------------------------------


@dataclass(frozen=True)
class PersonFeature:
    """One candidate row for the detector.

    The ``vector`` is a feature embedding in some shared space (e.g.
    co-association profile, document topic vector). The detector does
    not care how the vector is produced — it only does cosine over
    centroids.
    """

    person_grace_id: str
    vector: tuple[float, ...]
    display_name: str | None = None


@dataclass
class ClassificationResult:
    """One classification + persistence intent.

    ``DriftClassification`` is the operator-facing payload; the
    detector also returns the centroid similarity it observed so
    callers can trace the band assignment without re-running the math.
    The similarity number is internal — it never reaches DOM.
    """

    classification: DriftClassification
    similarity: float
    auto_assigned: bool


# ----- Math --------------------------------------------------------


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity. Returns 0.0 on zero-length or mismatched
    vectors (treated as "no signal" rather than raising — drift
    detection should not crash on a malformed input row).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return num / (norm_a * norm_b)


def _band_for(similarity: float, config: DriftConfig) -> DriftBand:
    if similarity >= config.auto_assign_threshold:
        return "high"
    if similarity >= config.queue_with_guess_threshold:
        return "medium"
    return "low"


# ----- Centroid extraction -----------------------------------------


def compute_centroids(
    matrix: PermissionMatrix,
    member_vectors: dict[str, Sequence[float]],
) -> dict[str, tuple[float, ...]]:
    """Mean feature vector per cluster.

    Members whose ``person_grace_id`` is missing from ``member_vectors``
    are skipped. A cluster with no resolvable members produces no
    centroid (the cluster is effectively invisible to the detector).
    """
    out: dict[str, tuple[float, ...]] = {}
    for cluster in matrix.role_clusters:
        vecs: list[Sequence[float]] = []
        for m in cluster.members:
            v = member_vectors.get(m.person_grace_id)
            if v is None:
                continue
            vecs.append(v)
        if not vecs:
            continue
        dim = len(vecs[0])
        if dim == 0:
            continue
        # All vectors expected to share dim; defensive guard:
        normed = [v for v in vecs if len(v) == dim]
        if not normed:
            continue
        centroid = tuple(
            sum(v[i] for v in normed) / len(normed) for i in range(dim)
        )
        out[cluster.cluster_id] = centroid
    return out


# ----- Classification ----------------------------------------------


def classify(
    candidates: Iterable[PersonFeature],
    matrix: PermissionMatrix,
    member_vectors: dict[str, Sequence[float]],
    *,
    config: DriftConfig | None = None,
) -> list[ClassificationResult]:
    """Classify each candidate by cosine to nearest cluster centroid.

    Stable output order: identical to the order of ``candidates`` as
    iterated. Tied similarities resolve to the first cluster
    encountered (cluster_id sorted ascending for determinism).
    """
    cfg = config or DriftConfig()
    centroids = compute_centroids(matrix, member_vectors)

    out: list[ClassificationResult] = []
    sorted_cluster_ids = sorted(centroids.keys())

    for candidate in candidates:
        best_id: str | None = None
        best_sim: float = -1.0
        for cid in sorted_cluster_ids:
            sim = _cosine(candidate.vector, centroids[cid])
            if sim > best_sim:
                best_sim = sim
                best_id = cid

        # No cluster centroid resolvable → low-band, blind queue.
        if best_id is None or best_sim < 0.0:
            band: DriftBand = "low"
            best_id = None
            similarity = 0.0
        else:
            similarity = best_sim
            band = _band_for(similarity, cfg)

        # Low band suppresses pre-filled guess (queue blind).
        proposed_cluster_id = best_id if band != "low" else None

        rationale = _rationale(band, similarity)
        cls = DriftClassification(
            person_grace_id=candidate.person_grace_id,
            proposed_cluster_id=proposed_cluster_id,
            drift_band=band,
            rationale=rationale,
        )
        out.append(
            ClassificationResult(
                classification=cls,
                similarity=similarity,
                auto_assigned=(band == "high"),
            )
        )
    return out


def _rationale(band: DriftBand, similarity: float) -> str:
    """Operator-facing rationale string. Uses band labels only — the
    raw similarity is not surfaced (D120/D217)."""
    if band == "high":
        return "Strong cluster centroid match; auto-assigned."
    if band == "medium":
        return "Partial cluster match; pre-filled guess for review."
    return "No strong cluster match; queued for manual review."


# ----- Persistence -------------------------------------------------


def persist_classifications(
    session,
    results: Sequence[ClassificationResult],
    *,
    observation_time: datetime,
    telemetry_emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, int]:
    """Insert one ``permission_drift_queue`` row per classification.

    High-band classifications additionally emit
    ``permission_matrix_auto_assigned`` telemetry via ``telemetry_emit``
    (a stub callable is used in dry-run / tests).

    Returns a band-count summary suitable for CLI stdout.
    """
    from sqlalchemy import text  # local import — no module-time SA cost

    band_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    sql = text(
        """
        INSERT INTO permission_drift_queue (
            person_grace_id,
            proposed_cluster_id,
            drift_band,
            status,
            details,
            created_at
        ) VALUES (
            :person_grace_id,
            :proposed_cluster_id,
            :drift_band,
            :status,
            CAST(:details AS JSONB),
            :created_at
        )
        """
    )

    for result in results:
        cls = result.classification
        # High-band auto-assignments mark the queue row as decided
        # (the operator is informed via telemetry; the queue still
        # carries the audit row).
        status = "decided" if result.auto_assigned else "pending"
        details = {
            "band": cls.drift_band,
            "rationale": cls.rationale,
            "auto_assigned": result.auto_assigned,
        }
        session.execute(
            sql,
            {
                "person_grace_id": cls.person_grace_id,
                "proposed_cluster_id": cls.proposed_cluster_id,
                "drift_band": cls.drift_band,
                "status": status,
                "details": json.dumps(details),
                "created_at": observation_time,
            },
        )
        band_counts[cls.drift_band] += 1

        if result.auto_assigned:
            # Best-effort metric increment (CP10, D337). Pinned to the
            # three-band kNN classification; cardinality 3.
            try:  # pragma: no cover — exercised in metric-contract test
                from src.analytics.metrics import (
                    record_permission_drift_auto_assignment,
                )

                record_permission_drift_auto_assignment(drift_band=cls.drift_band)
            except Exception:  # noqa: BLE001
                pass

            tel_payload = {
                "person_grace_id": cls.person_grace_id,
                "cluster_id": cls.proposed_cluster_id,
                "drift_band": cls.drift_band,
            }
            if telemetry_emit is not None:
                telemetry_emit(
                    "permission_matrix_auto_assigned",
                    tel_payload,
                )
            elif cls.proposed_cluster_id:
                try:
                    from src.elicitation.bridge import enqueue_event

                    enqueue_event(
                        event_type="permission_matrix_auto_assigned",
                        payload={
                            "person_grace_id": cls.person_grace_id,
                            "cluster_id": cls.proposed_cluster_id,
                            "drift_band": cls.drift_band,
                        },
                    )
                except Exception:  # noqa: BLE001
                    pass

    return band_counts


# ----- Run -----------------------------------------------------------


@dataclass
class DriftRunReport:
    """Top-level CLI output."""

    run_id: UUID
    observation_time: datetime
    dry_run: bool
    band_counts: dict[str, int] = field(
        default_factory=lambda: {"high": 0, "medium": 0, "low": 0}
    )
    classifications: list[ClassificationResult] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "observation_time": self.observation_time.isoformat(),
            "dry_run": self.dry_run,
            "band_counts": dict(self.band_counts),
            "classifications": [
                {
                    "person_grace_id": r.classification.person_grace_id,
                    "proposed_cluster_id": r.classification.proposed_cluster_id,
                    "drift_band": r.classification.drift_band,
                    "rationale": r.classification.rationale,
                    "auto_assigned": r.auto_assigned,
                }
                for r in self.classifications
            ],
        }


def run_once(
    *,
    matrix: PermissionMatrix,
    candidates: Sequence[PersonFeature],
    member_vectors: dict[str, Sequence[float]],
    observation_time: datetime,
    dry_run: bool,
    config: DriftConfig | None = None,
    session: Any | None = None,
    telemetry_emit: Callable[[str, dict[str, Any]], None] | None = None,
) -> DriftRunReport:
    """One drift detection run.

    Persistence is skipped in ``dry_run`` mode AND when ``session`` is
    None (the CLI loads the session lazily; tests pass a stub).
    """
    results = classify(candidates, matrix, member_vectors, config=config)
    band_counts: dict[str, int] = {"high": 0, "medium": 0, "low": 0}

    if not dry_run and session is not None:
        band_counts = persist_classifications(
            session,
            results,
            observation_time=observation_time,
            telemetry_emit=telemetry_emit,
        )
    else:
        for r in results:
            band_counts[r.classification.drift_band] += 1

    return DriftRunReport(
        run_id=uuid4(),
        observation_time=observation_time,
        dry_run=dry_run,
        band_counts=band_counts,
        classifications=list(results),
    )


# ----- CLI ---------------------------------------------------------


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.permissions.drift_detector",
        description=(
            "kNN drift detector (D337). CLI-only per D246; never wire "
            "this into FastAPI or APScheduler."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="Run kNN drift detection once")
    run.add_argument(
        "--observation-time",
        default=None,
        help="ISO8601 observation time (defaults to now, UTC).",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip DB writes; emit deterministic JSON to stdout.",
    )
    run.add_argument(
        "--job-id",
        default=None,
        help="UUID of an existing drift_runs row (API-INSERT-first, D460).",
    )
    return parser.parse_args(list(argv))


def _empty_run_report(
    observation_time: datetime,
    dry_run: bool,
) -> DriftRunReport:
    return DriftRunReport(
        run_id=uuid4(),
        observation_time=observation_time,
        dry_run=dry_run,
    )


def _get_drift_db_session():
    """Lazy import to avoid hard dependency on database at import time."""
    from src.shared.database import get_session_factory
    return get_session_factory()()


def _drift_runs_insert(session, *, run_id: UUID, obs: datetime, dry_run: bool) -> None:
    """INSERT a drift_runs row for CLI-initiated runs (D460)."""
    from sqlalchemy import text
    session.execute(text("""
        INSERT INTO drift_runs (id, run_id, observation_time, dry_run, started_at, status, triggered_by, summary_json)
        VALUES (:id, :run_id, :observation_time, :dry_run, now(), 'running', 'cli', '{}')
    """), {"id": str(run_id), "run_id": str(run_id), "observation_time": obs, "dry_run": dry_run})
    session.commit()


def _drift_runs_update(session, *, job_id: UUID, status: str, summary: dict, error_message: str | None) -> int:
    """UPDATE a drift_runs row on completion (D460). Returns rowcount."""
    from sqlalchemy import text
    result = session.execute(text("""
        UPDATE drift_runs
        SET completed_at = now(), status = :status, summary_json = :summary, error_message = :error_message
        WHERE id = :id
    """), {"id": str(job_id), "status": status, "summary": json.dumps(summary), "error_message": error_message})
    session.commit()
    return result.rowcount


def cmd_run(args: argparse.Namespace) -> int:
    if args.observation_time:
        try:
            obs = datetime.fromisoformat(args.observation_time)
        except ValueError:
            sys.stderr.write(
                "drift.run: --observation-time must be ISO8601\n"
            )
            return 2
        if obs.tzinfo is None:
            obs = obs.replace(tzinfo=timezone.utc)
    else:
        obs = datetime.now(tz=timezone.utc)

    # D460: drift_runs persistence
    job_id: UUID | None = None
    if args.job_id:
        try:
            job_id = UUID(args.job_id)
        except ValueError:
            sys.stderr.write("drift.run: --job-id must be a valid UUID\n")
            return 2

    session = None
    try:
        session = _get_drift_db_session()
    except Exception:  # noqa: BLE001
        _log.warning("drift.run.db_unavailable")

    # When --job-id is absent (direct CLI use), self-INSERT
    if session is not None and job_id is None:
        job_id = uuid4()
        try:
            _drift_runs_insert(session, run_id=job_id, obs=obs, dry_run=bool(args.dry_run))
        except Exception:  # noqa: BLE001
            _log.warning("drift.run.insert_failed", job_id=str(job_id))

    # CLI invoked with no live evidence wiring — emit the empty
    # observation so launchd / cron runs are no-op safe and operators
    # see a stable JSON envelope. Wiring to a live evidence source is
    # deferred to a future chunk; the function ``run_once`` is the
    # programmatic entry point used by tests and Chunk-44 callers.
    report = _empty_run_report(observation_time=obs, dry_run=bool(args.dry_run))
    run_status = "success"
    error_message = None

    sys.stdout.write(json.dumps(report.to_json()))
    sys.stdout.write("\n")

    # UPDATE drift_runs on completion
    if session is not None and job_id is not None:
        try:
            rowcount = _drift_runs_update(
                session,
                job_id=job_id,
                status=run_status,
                summary=report.to_json(),
                error_message=error_message,
            )
            if rowcount == 0:
                _log.warning(
                    "drift.run.update_no_rows",
                    job_id=str(job_id),
                    msg="No drift_runs row found for job_id — possibly stale or deleted",
                )
        except Exception:  # noqa: BLE001
            _log.warning("drift.run.update_failed", job_id=str(job_id))

    if session is not None:
        session.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "run":
        return cmd_run(args)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "ClassificationResult",
    "DEFAULT_AUTO_ASSIGN_THRESHOLD",
    "DEFAULT_QUEUE_WITH_GUESS_THRESHOLD",
    "DriftConfig",
    "DriftRunReport",
    "PersonFeature",
    "classify",
    "compute_centroids",
    "main",
    "persist_classifications",
    "run_once",
]
