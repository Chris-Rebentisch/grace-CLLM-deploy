"""Confidence decay batch (Chunk 35a, D264).

Applies exponential half-life decay to verified graph entities and
edges in ArcadeDB:

    c_new = max(verdict_floor, c_at_verification * 2 ** (-Δt / t_half))

`Δt = observation_time - last_verified_at` where `observation_time`
is operator-supplied via CLI argument. The transform does **not** call
``datetime.now()`` internally — ``decay_run`` is idempotent under a
fixed clock parameter (D264 idempotency contract).

Scope boundary (v3 B1 resolution).
This module reads from and writes to ArcadeDB **only**. It must not
import SQLAlchemy sessions or write to the Postgres ``extraction_claims``
table; that table retains its extraction-time confidence as immutable
historical provenance. F8 (build prompt §3) enforces this with a hard
fail.

Operational model.
CLI-only invocation per D246 (mirrors signal pipeline / correlation
engine / eval suite). No FastAPI lifespan scheduler, no APScheduler.
Operators schedule via host cron / launchd. See
``scripts/launchd/grace.confidence-decay.plist.example``.

Graph property names (R10).
Decayed runtime confidence is stored on ArcadeDB vertices and edges
under the existing ``extraction_confidence`` column. The decay batch
also reads two new properties:

* ``confidence_at_verification`` — the confidence at the moment a
  human/verifier promoted the entity/edge. Set once at verification
  time; never overwritten by the decay batch.
* ``last_verified_at`` — ISO-8601 timestamp of the most recent
  verification event. Used to compute Δt.
* ``verdict`` — Chunk 19 verification verdict
  (``SUPPORTED``/``INSUFFICIENT``/``REFUTED``); selects the floor.

Entities or edges missing any of these three properties are skipped
(no decay possible without a verified-at-time anchor). The decay batch
emits a warning per skipped row at ``--verbose``.

Observability (D264 + Chunk 67 D453).
Each batch run emits three named OTel instruments registered in
``src/analytics/metrics.py``:

* ``grace_decay_batch_rows_processed`` (counter) — labeled by
  ``verdict`` and ``ontology_module``.
* ``grace_decay_batch_rows_actually_mutated`` (counter) — rows where
  persisted ``extraction_confidence`` changed beyond epsilon (D453).
* ``grace_decay_batch_duration_seconds`` (histogram).

Instrument names are allowlisted in ``tests/analytics/test_metric_contract.py``
(``GOLDEN_NAMES``); extend that set when adding decay metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from pydantic import BaseModel, Field

from src.graph.arcade_client import ArcadeClient, get_arcade_client

log = structlog.get_logger()


# --- Pydantic config -------------------------------------------------------


class DecayConfig(BaseModel):
    """Half-life decay configuration loaded from ``config/decay_config.yaml``.

    The verdict floors mirror the Chunk 19 verdict-band floors registered
    in ``src/extraction/confidence_scorer.py`` (D264).
    """

    t_half_days: float = Field(
        default=180.0, gt=0, description="Default half-life in days. Must be > 0."
    )
    per_relationship_overrides: dict[str, float] = Field(
        default_factory=dict,
        description=(
            "Relationship-type to half-life-days override map. Empty default."
        ),
    )
    verdict_floors: dict[str, float] = Field(
        default_factory=lambda: {
            "SUPPORTED": 0.5,
            "INSUFFICIENT": 0.5,
            "REFUTED": 0.05,
        },
        description=(
            "Per-verdict confidence floor. Decayed confidence cannot drop "
            "below the floor for that verdict band."
        ),
    )
    default_confidence_at_verification: float = Field(
        default=0.9, ge=0.0, le=1.0,
        description="Default confidence_at_verification stamped on accepted claims"
    )
    rows_decayed_equality_epsilon: float = Field(
        default=1e-9, ge=0.0,
        description="Epsilon threshold for honest mutation counter"
    )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "DecayConfig":
        """Load a ``DecayConfig`` from a YAML file."""
        with open(path, "r", encoding="utf-8") as fh:
            payload = yaml.safe_load(fh) or {}
        return cls.model_validate(payload)


# --- Pure decay transform --------------------------------------------------


def decay_confidence(
    c_at_verification: float,
    delta_days: float,
    t_half: float,
    floor: float,
) -> float:
    """Apply half-life exponential decay with a verdict floor.

    Returns ``max(floor, c_at_verification * 2 ** (-delta_days / t_half))``.

    Idempotent under the same inputs (D264 idempotency contract).

    Raises:
        ValueError: if ``delta_days`` is negative (clock went backwards) or
            ``t_half`` is non-positive.
    """
    if delta_days < 0:
        raise ValueError(
            f"delta_days must be >= 0; observation_time precedes last_verified_at "
            f"(delta_days={delta_days})"
        )
    if t_half <= 0:
        raise ValueError(f"t_half must be > 0; got {t_half}")
    decayed = c_at_verification * (2.0 ** (-delta_days / t_half))
    return max(floor, decayed)


# --- Batch result models ---------------------------------------------------


@dataclass
class DecayResult:
    """Outcome of a decay batch run.

    All counts are scoped to a single ``decay_run`` invocation.
    """

    rows_processed: int = 0
    rows_decayed: int = 0
    rows_skipped: int = 0
    rows_actually_mutated: int = 0
    rows_skipped_no_verification_metadata: int = 0
    rows_floored: int = 0
    duration_seconds: float = 0.0
    dry_run: bool = False
    per_verdict_counts: dict[str, int] = field(default_factory=dict)


# --- Batch runner ----------------------------------------------------------


_VERIFIED_QUERY = (
    "MATCH (n) WHERE n.last_verified_at IS NOT NULL "
    "AND n.confidence_at_verification IS NOT NULL "
    "AND n.verdict IS NOT NULL "
    "RETURN n"
)
_VERIFIED_EDGE_QUERY = (
    "MATCH ()-[r]->() WHERE r.last_verified_at IS NOT NULL "
    "AND r.confidence_at_verification IS NOT NULL "
    "AND r.verdict IS NOT NULL "
    "RETURN r"
)


def _parse_iso(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _select_t_half(
    config: DecayConfig, relationship_type: str | None
) -> float:
    if relationship_type and relationship_type in config.per_relationship_overrides:
        return float(config.per_relationship_overrides[relationship_type])
    return float(config.t_half_days)


def _select_floor(config: DecayConfig, verdict: str | None) -> float:
    if verdict is None:
        return 0.0
    return float(config.verdict_floors.get(verdict, 0.0))


async def _persist(
    client: ArcadeClient,
    grace_id: str,
    new_confidence: float,
    is_edge: bool,
) -> None:
    """Update ``extraction_confidence`` on the vertex/edge identified by
    ``grace_id``.
    """
    if is_edge:
        cypher = (
            "MATCH ()-[r {grace_id: $grace_id}]->() "
            "SET r.extraction_confidence = $c"
        )
    else:
        cypher = (
            "MATCH (n {grace_id: $grace_id}) "
            "SET n.extraction_confidence = $c"
        )
    await client.execute_cypher(
        cypher,
        params={"grace_id": grace_id, "c": float(new_confidence)},
    )


def _extract_records(payload: Any, key: str) -> list[dict]:
    """Pull ``[{...}, ...]`` rows out of an ArcadeDB query payload."""
    if not isinstance(payload, dict):
        return []
    rows = payload.get("result") or []
    out: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        node = row.get(key) if isinstance(row.get(key), dict) else row
        if isinstance(node, dict):
            out.append(node)
    return out


async def decay_run(
    observation_time: datetime,
    config: DecayConfig,
    client: ArcadeClient | None = None,
    dry_run: bool = False,
) -> DecayResult:
    """Apply half-life decay to all verified graph entities and edges.

    Reads verified entities/edges from ArcadeDB, applies
    ``decay_confidence()`` per row, and persists the new
    ``extraction_confidence`` value unless ``dry_run`` is True.

    Emits ``grace_decay_batch_rows_processed`` (counter, labels
    ``verdict`` + ``ontology_module``) and
    ``grace_decay_batch_duration_seconds`` (histogram).

    Does **not** read or write Postgres ``extraction_claims`` (F8).
    """
    # Lazy import so test modules can monkeypatch metrics module
    # before this function is called.
    from src.analytics.metrics import (
        decay_batch_duration,
        decay_batch_rows_actually_mutated,
        decay_batch_rows_processed,
    )

    started = time.monotonic()
    result = DecayResult(dry_run=dry_run)

    arcade = client or get_arcade_client()

    entity_payload = await arcade.execute_cypher(_VERIFIED_QUERY)
    edge_payload = await arcade.execute_cypher(_VERIFIED_EDGE_QUERY)

    entities = _extract_records(entity_payload, "n")
    edges = _extract_records(edge_payload, "r")

    rows: list[tuple[dict, bool]] = [(e, False) for e in entities] + [
        (e, True) for e in edges
    ]

    for row, is_edge in rows:
        result.rows_processed += 1

        c_at = row.get("confidence_at_verification")
        verified_at = _parse_iso(row.get("last_verified_at"))
        verdict = row.get("verdict")
        grace_id = row.get("grace_id")
        relationship_type = row.get("relationship_type") or row.get("@type")
        ontology_module = row.get("ontology_module") or "_unknown_"

        if (
            c_at is None
            or verified_at is None
            or verdict is None
            or grace_id is None
        ):
            result.rows_skipped += 1
            # D453 — sub-counter for verification-metadata absence only
            # (the three decay-eligibility fields). grace_id-only miss
            # does NOT increment this sub-counter.
            if c_at is None or verified_at is None or verdict is None:
                result.rows_skipped_no_verification_metadata += 1
            log.warning(
                "decay.skipped_missing_property",
                grace_id=grace_id,
                has_c_at=c_at is not None,
                has_verified_at=verified_at is not None,
                has_verdict=verdict is not None,
            )
            continue

        delta_days = (observation_time - verified_at).total_seconds() / 86400.0
        t_half = _select_t_half(config, relationship_type)
        floor = _select_floor(config, verdict)

        try:
            new_c = decay_confidence(
                c_at_verification=float(c_at),
                delta_days=delta_days,
                t_half=t_half,
                floor=floor,
            )
        except ValueError as exc:
            result.rows_skipped += 1
            log.warning(
                "decay.skipped_invalid_input",
                grace_id=grace_id,
                error=str(exc),
            )
            continue

        old_c = row.get("extraction_confidence")

        if new_c <= floor + 1e-9:
            result.rows_floored += 1
        if not dry_run:
            await _persist(arcade, str(grace_id), new_c, is_edge=is_edge)
        result.rows_decayed += 1

        # D453 — honest mutation counter: only fires when the new
        # extraction_confidence actually differs beyond epsilon.
        if old_c is not None and abs(new_c - float(old_c)) > config.rows_decayed_equality_epsilon:
            result.rows_actually_mutated += 1
            decay_batch_rows_actually_mutated.add(1)
        elif old_c is None:
            # First-time write (no prior extraction_confidence) counts
            # as an actual mutation.
            result.rows_actually_mutated += 1
            decay_batch_rows_actually_mutated.add(1)

        result.per_verdict_counts[verdict] = (
            result.per_verdict_counts.get(verdict, 0) + 1
        )

        decay_batch_rows_processed.add(
            1,
            attributes={"verdict": verdict, "ontology_module": ontology_module},
        )

    duration = time.monotonic() - started
    result.duration_seconds = duration
    decay_batch_duration.record(duration)

    log.info(
        "decay.run_complete",
        observation_time=observation_time.isoformat(),
        rows_processed=result.rows_processed,
        rows_decayed=result.rows_decayed,
        rows_skipped=result.rows_skipped,
        rows_floored=result.rows_floored,
        dry_run=dry_run,
        duration_seconds=round(duration, 3),
    )

    return result


# --- CLI -------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.extraction.confidence_decay",
        description=(
            "GrACE confidence decay batch (D264). Applies half-life decay "
            "to verified graph entities and edges. Idempotent under a fixed "
            "--observation-time clock."
        ),
    )
    parser.add_argument(
        "--observation-time",
        required=True,
        help=(
            "ISO 8601 timestamp used as the decay clock anchor. Required; "
            "the transform does not call NOW() internally."
        ),
    )
    parser.add_argument(
        "--config",
        default="config/decay_config.yaml",
        help="Path to decay config YAML (default: config/decay_config.yaml).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute decayed values without persisting to ArcadeDB.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose structlog output (skipped-row warnings).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    from src.shared._logging_utils import clamp_http_client_logs
    clamp_http_client_logs()

    obs = _parse_iso(args.observation_time)
    if obs is None:
        sys.stderr.write(
            f"ERROR: --observation-time {args.observation_time!r} is not a "
            f"valid ISO 8601 timestamp.\n"
        )
        return 2

    config_path = Path(args.config)
    if not config_path.exists():
        sys.stderr.write(f"ERROR: config file not found: {config_path}\n")
        return 2
    config = DecayConfig.from_yaml(config_path)

    asyncio.run(decay_run(observation_time=obs, config=config, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
