"""CLI-only calibration updater for earned autonomy (Chunk 49, D394–D396).

D246 mirror: NEVER import this module from any route module or FastAPI
lifespan. The only sanctioned entry point is the CLI:

    python -m src.ontology.calibration_updater run [--dry-run] [--tier 1|2|3]

Invariant: D246 CLI-only (calibration updater).
Carve-out: none.
Authorization source: chunk-49-spec-v6-FINAL.md §4 / §6 Step 5.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

import structlog
import yaml
from sqlalchemy.orm import Session

from src.ontology.calibration import (
    compute_calibration_bands,
    compute_trust_score,
    detect_regression,
)
from src.ontology.database import (
    CalibrationRecordRow,
    create_calibration_record,
    delete_calibration_records_for_tier,
    get_calibration_decisions_for_tier,
    get_calibration_records_for_tier,
    get_trust_score_for_tier,
    upsert_trust_score,
)
from src.ontology.models import CalibrationRecord
from src.shared.database import get_session_factory

log = structlog.get_logger()

_DEFAULT_CONFIG_PATH = "config/calibration.yaml"


def _load_config(path: str = _DEFAULT_CONFIG_PATH) -> dict:
    """Load calibration config from YAML."""
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        log.warning("calibration_config_not_found", path=path)
        return {}


def run_updater(
    db: Session,
    *,
    tiers: list[int] | None = None,
    dry_run: bool = False,
    observation_time: datetime | None = None,
    config: dict | None = None,
) -> dict[int, dict]:
    """Run calibration updater for specified tiers (default: all 1-3).

    Reads calibration_decisions, reads prior calibration_records bands,
    recomputes bands → deletes-and-repopulates calibration_records,
    recomputes trust → updates trust_scores, detects regressions.

    Returns per-tier result dict.
    """
    if tiers is None:
        tiers = [1, 2, 3]
    if config is None:
        config = _load_config()
    if observation_time is None:
        observation_time = datetime.now(UTC)

    band_width = config.get("band_width", 0.10)
    sparse_band_floor = config.get("sparse_band_floor", 5)
    regression_sensitivity = config.get("regression_sensitivity", 0.10)

    results: dict[int, dict] = {}

    for tier in tiers:
        tier_log = log.bind(tier=tier)
        tier_log.info("calibration_updater.tier_start")

        # 1. Read all decisions for this tier.
        decisions = get_calibration_decisions_for_tier(db, tier)
        tier_log.info("calibration_updater.decisions_read", count=len(decisions))

        # 2. Read current trust_scores row (create with defaults if absent — cold start).
        trust_row = get_trust_score_for_tier(db, tier)
        window_size = trust_row.window_size if trust_row else config.get("default_window_size", 50)

        # 3. Compute new bands.
        bands = compute_calibration_bands(decisions, band_width=band_width)

        # 4. Read prior band state BEFORE delete (for regression baseline).
        prior_records = get_calibration_records_for_tier(db, tier)
        prior_rates: dict[tuple[float, float], float] = {
            (r.confidence_band_low, r.confidence_band_high): r.approval_rate
            for r in prior_records
        }

        # 5. Compute trust score.
        trust_score = compute_trust_score(decisions, window_size=window_size)

        # 6. Detect regression per non-sparse band.
        any_regression = False
        band_regressions = []
        for band in bands:
            if band.sample_count < sparse_band_floor:
                continue
            historical_rate = prior_rates.get(
                (band.band_low, band.band_high), 1.0
            )
            reg_result = detect_regression(
                historical_rate=historical_rate,
                recent_rate=band.approval_rate,
                recent_n=band.sample_count,
                sensitivity=regression_sensitivity,
                sparse_band_floor=sparse_band_floor,
            )
            if reg_result.regression_detected:
                any_regression = True
                band_regressions.append({
                    "band": (band.band_low, band.band_high),
                    "historical_rate": historical_rate,
                    "recent_rate": band.approval_rate,
                    "lower_ci": reg_result.lower_ci,
                })

        if not dry_run:
            # 7. Delete-and-repopulate calibration_records.
            delete_calibration_records_for_tier(db, tier)
            for band in bands:
                record = CalibrationRecord(
                    change_tier=tier,
                    confidence_band_low=band.band_low,
                    confidence_band_high=band.band_high,
                    approval_rate=band.approval_rate,
                    sample_count=band.sample_count,
                    trust_score=trust_score,
                    autonomy_threshold=trust_row.autonomy_threshold if trust_row else 0.95,
                    autonomy_enabled=False,
                    window_size=window_size,
                    risk_tolerance=trust_row.risk_tolerance if trust_row else 0.95,
                )
                create_calibration_record(db, record)

            # 8. Upsert trust_scores row.
            upsert_trust_score(
                db,
                tier=tier,
                trust_score=trust_score,
                total_decisions=len(decisions),
                regression_detected=any_regression,
                last_computed_at=observation_time,
            )

            # 9. OTel counter.
            try:
                from src.analytics.metrics import record_calibration_updater_run

                outcome = "regression_detected" if any_regression else "success"
                record_calibration_updater_run(tier=str(tier), outcome=outcome)
            except Exception:  # noqa: BLE001
                pass

        tier_result = {
            "decisions_count": len(decisions),
            "bands_count": len(bands),
            "trust_score": trust_score,
            "regression_detected": any_regression,
            "band_regressions": band_regressions,
            "dry_run": dry_run,
        }
        results[tier] = tier_result
        tier_log.info("calibration_updater.tier_complete", **tier_result)

    return results


def main(argv: list[str] | None = None) -> None:
    """CLI entry point."""
    # F-0049/ISS-0040: mirror this D246 subprocess's OTel metrics into the
    # prometheus multiproc dir (no-op when PROMETHEUS_MULTIPROC_DIR is unset).
    from src.analytics.subprocess_metrics import init_subprocess_metrics

    init_subprocess_metrics()

    parser = argparse.ArgumentParser(
        description="Earned autonomy calibration updater (Chunk 49, D394–D396).",
    )
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run calibration updater")
    run_parser.add_argument("--dry-run", action="store_true", help="Skip DB writes")
    run_parser.add_argument(
        "--tier", type=int, choices=[1, 2, 3],
        help="Run for a specific tier only",
    )
    run_parser.add_argument(
        "--observation-time", type=str, default=None,
        help="ISO 8601 observation time override",
    )

    args = parser.parse_args(argv)
    if args.command != "run":
        parser.print_help()
        sys.exit(1)

    tiers = [args.tier] if args.tier else None
    observation_time = (
        datetime.fromisoformat(args.observation_time) if args.observation_time else None
    )

    db = get_session_factory()()
    try:
        results = run_updater(
            db,
            tiers=tiers,
            dry_run=args.dry_run,
            observation_time=observation_time,
        )
        for tier, result in results.items():
            log.info("calibration_updater.result", tier=tier, **result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
