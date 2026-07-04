"""Correlation engine configuration (Chunk 33, D248/D250/D252).

Loaded from ``config/correlation_engine.yaml``. Defaults match the build
spec; per-pattern thresholds and the raw-metric allowlist are operator-
tunable.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


_DEFAULT_CONFIG_PATH = Path("config/correlation_engine.yaml")


_DEFAULT_RAW_METRIC_ALLOWLIST = [
    "grace_retrieval_strategy_contributions",
    "grace_retrieval_zero_results",
    "http_server_request_duration_seconds",
    "grace_extraction_triple_confidence",
]


class CorrelationEngineConfig(BaseModel):
    """Root correlation-engine configuration."""

    prometheus_url: str = "http://127.0.0.1:9090"
    baseline_window_days: int = 14
    current_window_days: int = 1
    sigma_multiplier: float = 3.0
    mann_kendall_min_points: int = 10
    mann_kendall_alpha: float = 0.05
    latest_value_floor_mine: float = 0.7
    emit_threshold: float = 0.0
    # F-0038 / ISS-0027: per-signal `run-all --signal X` invocations persist
    # each signal type in its own signal_runs row; a latest-run-only read
    # blanked every conjunction. Detectors now window across ALL successful
    # runs within this lookback instead of joining the single newest run.
    signal_lookback_hours: float = Field(
        default=24.0,
        gt=0.0,
        description=(
            "Lookback window (hours) for reading analytics_signals: for each "
            "signal_type/ontology_module, the correlation engine uses the most "
            "recently detected signal across all successful signal_runs within "
            "this window, rather than only signals from the single newest run "
            "(F-0038/ISS-0027 — per-signal runs fragment signals across runs)."
        ),
    )
    raw_metric_allowlist: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_RAW_METRIC_ALLOWLIST)
    )


def load_config(path: str | Path | None = None) -> CorrelationEngineConfig:
    """Load YAML config from ``path`` (default config/correlation_engine.yaml).

    Missing file returns defaults.
    """
    p = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    if not p.exists():
        return CorrelationEngineConfig()
    raw = yaml.safe_load(p.read_text()) or {}
    return CorrelationEngineConfig.model_validate(raw)
