"""Calibration API routes — dashboard, bands, trust, config (Chunk 49, D394–D397).

D246 invariant: this module does NOT import ``src.ontology.calibration_updater``.
The updater runs CLI-only. CI guard at
``tests/ontology/test_calibration_route_isolation.py`` enforces.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.ontology.database import (
    CalibrationRecordRow,
    get_calibration_records_for_tier,
    get_trust_score_for_tier,
    TrustScoreRow,
)
from src.ontology.models import (
    CalibrationBand,
    CalibrationDashboard,
    TierDashboard,
    TierProgress,
    TrustScore,
)
from src.shared.database import get_db

logger = structlog.get_logger()

router = APIRouter(prefix="/api/ontology/calibration", tags=["calibration"])


# --- Request/response models ---


class ConfigPatchRequest(BaseModel):
    """Body for PATCH /api/ontology/calibration/config/{tier}."""

    model_config = ConfigDict(extra="forbid")

    risk_tolerance: float | None = Field(
        default=None, ge=0.80, le=0.99,
        description="Acceptable approval rate (0.80–0.99)",
    )
    window_size: int | None = Field(
        default=None, ge=20, le=200,
        description="Rolling window size (20–200)",
    )
    min_reviews_for_calibration: int | None = Field(
        default=None, ge=10, le=200,
        description="Minimum reviews before calibration (10–200)",
    )


# --- Helpers ---


def _validate_tier(tier: int) -> None:
    if tier not in (1, 2, 3):
        raise HTTPException(status_code=422, detail="Tier must be 1, 2, or 3")


def _get_trust_indicator(ts: TrustScore) -> str:
    """Derive three-band trust indicator from trust score state."""
    if ts.total_decisions < ts.min_reviews_for_calibration:
        return "insufficient"
    if ts.trust_score >= ts.autonomy_threshold:
        return "high"
    return "building"


def _build_tier_dashboard(tier: int, db: Session) -> TierDashboard:
    """Build dashboard payload for one tier."""
    ts = get_trust_score_for_tier(db, tier)

    if ts is None:
        # Cold start — no trust_scores row yet.
        default_ts = TrustScore(tier=tier)
        return TierDashboard(
            tier=tier,
            bands=[],
            trust_indicator="insufficient",
            progress=TierProgress(
                total_decisions=0,
                min_reviews_for_calibration=50,
                progress_label="0 of 50 reviews",
            ),
            trust_score_state=default_ts,
        )

    # Read bands from calibration_records.
    records = get_calibration_records_for_tier(db, tier)
    bands = [
        CalibrationBand(
            band_low=r.confidence_band_low,
            band_high=r.confidence_band_high,
            approval_rate=r.approval_rate,
            sample_count=r.sample_count,
        )
        for r in records
    ]

    trust_indicator = _get_trust_indicator(ts)
    progress_label = f"{ts.total_decisions} of {ts.min_reviews_for_calibration} reviews"

    return TierDashboard(
        tier=tier,
        bands=bands,
        trust_indicator=trust_indicator,
        progress=TierProgress(
            total_decisions=ts.total_decisions,
            min_reviews_for_calibration=ts.min_reviews_for_calibration,
            progress_label=progress_label,
        ),
        trust_score_state=ts,
    )


# --- Routes ---


@router.get("/dashboard")
async def get_dashboard(db: Session = Depends(get_db)) -> dict:
    """Aggregated calibration dashboard — per-tier bands, trust indicators, progress."""
    tiers = [_build_tier_dashboard(t, db) for t in (1, 2, 3)]
    dashboard = CalibrationDashboard(tiers=tiers)
    return dashboard.model_dump(mode="json")


@router.get("/bands/{tier}")
async def get_bands(tier: int, db: Session = Depends(get_db)) -> list[dict]:
    """Band list for one tier from calibration_records."""
    _validate_tier(tier)
    records = get_calibration_records_for_tier(db, tier)
    bands = [
        CalibrationBand(
            band_low=r.confidence_band_low,
            band_high=r.confidence_band_high,
            approval_rate=r.approval_rate,
            sample_count=r.sample_count,
        )
        for r in records
    ]
    return [b.model_dump(mode="json") for b in bands]


@router.get("/trust/{tier}")
async def get_trust(tier: int, db: Session = Depends(get_db)) -> dict:
    """Trust score state for one tier."""
    _validate_tier(tier)
    ts = get_trust_score_for_tier(db, tier)
    if ts is None:
        raise HTTPException(status_code=404, detail="No trust score data for this tier")
    return ts.model_dump(mode="json")


@router.patch("/config/{tier}")
async def patch_config(
    tier: int,
    body: ConfigPatchRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Update per-tier calibration configuration. Mutating — admin-key required."""
    _validate_tier(tier)

    row = db.query(TrustScoreRow).filter(TrustScoreRow.tier == tier).first()
    if row is None:
        raise HTTPException(status_code=404, detail="No trust score data for this tier")

    if body.risk_tolerance is not None:
        row.risk_tolerance = body.risk_tolerance
    if body.window_size is not None:
        row.window_size = body.window_size
    if body.min_reviews_for_calibration is not None:
        row.min_reviews_for_calibration = body.min_reviews_for_calibration

    db.commit()
    db.refresh(row)

    ts = TrustScore(
        tier=row.tier,
        trust_score=row.trust_score,
        autonomy_threshold=row.autonomy_threshold,
        autonomy_enabled=row.autonomy_enabled,
        window_size=row.window_size,
        min_reviews_for_calibration=row.min_reviews_for_calibration,
        risk_tolerance=row.risk_tolerance,
        total_decisions=row.total_decisions,
        regression_detected=row.regression_detected,
        last_computed_at=row.last_computed_at,
    )
    return ts.model_dump(mode="json")
