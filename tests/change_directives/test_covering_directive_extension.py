"""Chunk 39 D305 — CoveringDirective optional realization fields."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.change_directives.models import CoveringDirective


def _base_row(**extra):
    return {
        "directive_id": "550e8400-e29b-41d4-a716-446655440000",
        "title": "t",
        "tier": "Operational_Adjustment",
        "status": "active",
        "authored_at": datetime.now(timezone.utc).isoformat(),
        "affected_segments": ["finance"],
        **extra,
    }


def test_covering_directive_accepts_realization_fields():
    d = CoveringDirective.model_validate(
        _base_row(
            progress_percentage=0.42,
            velocity_band="steady",
            is_stalled=False,
        )
    )
    assert d.progress_percentage == 0.42
    assert d.velocity_band == "steady"
    assert d.is_stalled is False


def test_covering_directive_rejects_unknown_velocity_band():
    with pytest.raises(ValidationError):
        CoveringDirective.model_validate(
            _base_row(
                tier="Strategic_Initiative",
                velocity_band="hyperspeed",
            )
        )
