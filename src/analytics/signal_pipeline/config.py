"""Signal pipeline configuration (Chunk 32, D240, D245).

Loaded from ``config/signal_pipeline.yaml``. Per-signal sub-models hold
threshold/window knobs; global section holds Mann-Kendall + Prometheus
options. Sigma multiplier and window sizes are operator-tunable
(documented in the handoff).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


_DEFAULT_CONFIG_PATH = Path("config/signal_pipeline.yaml")


class _SignalCommon(BaseModel):
    enabled: bool = True
    emit_threshold: float = 0.0
    sigma_multiplier: float = 3.0
    baseline_window_days: int = 14
    current_window_days: int = 1


class SignalAConfig(_SignalCommon):
    # F-0041 / ISS-0034 (signal-surface follow-up) — cross-type duplicate
    # sub-mode. The ER fix made the resolver FLAG same-normalized-name
    # different-type collisions at mint time (resolution_note
    # "cross_type_name_collision" rows in entity_resolution_log), but nothing
    # surfaced them to operators — the validation-run case (Crestline Water
    # Authority as BOTH Legal_Entity AND Vendor) was only found by a human
    # tripping over it. These knobs gate an ALSO-emitted Signal A sub-mode
    # over that log substrate, following the F-0037 point-in-time config
    # pattern; the Prometheus trend behavior is unchanged.
    cross_type_collision_enabled: bool = Field(
        default=True,
        description=(
            "Enable the cross-type duplicate sub-mode (F-0041/ISS-0034): fire "
            "on recent entity_resolution_log rows whose resolution_note "
            "contains 'cross_type_name_collision' (same normalized name, "
            "different vertex type), independent of Prometheus history."
        ),
    )
    cross_type_window_days: int = Field(
        default=30,
        description=(
            "Lookback window (days) over entity_resolution_log.resolved_at "
            "for cross-type collision detection."
        ),
    )
    cross_type_count_threshold: int = Field(
        default=5,
        description=(
            "Distinct colliding names at which cross-type strength saturates "
            "to 1.0; strength = min(1.0, distinct_collisions / threshold) "
            "(D245 bounds)."
        ),
    )


class SignalBConfig(_SignalCommon):
    cooccur_in_chunk: bool = True
    cooccur_in_sentence: bool = True


class SignalCConfig(_SignalCommon):
    kind_filter: list[str] = Field(
        default_factory=lambda: ["invalid_entity_type", "schema_version_mismatch"]
    )


class SignalDConfig(_SignalCommon):
    # F-0037 / ISS-0028 — point-in-time firing mode. The Mann-Kendall trend
    # test needs >= mann_kendall_min_points days of per-type history, so a
    # single deprecated-type claim could mathematically never fire Signal D.
    # These knobs gate an ALSO-emitted point-in-time mode; trend behavior is
    # unchanged.
    point_in_time_enabled: bool = Field(
        default=True,
        description=(
            "Enable the point-in-time firing mode (F-0037/ISS-0028): fire on "
            "recent quarantined claims whose constraint_violations include "
            "rule 'deprecated_entity_type', without requiring trend history."
        ),
    )
    point_in_time_window_days: int = Field(
        default=7,
        description=(
            "Lookback window (days) for point-in-time deprecated-type claim "
            "detection."
        ),
    )
    point_in_time_count_threshold: int = Field(
        default=5,
        description=(
            "Occurrence count at which point-in-time strength saturates to "
            "1.0; strength = min(1.0, count / threshold) (D245 bounds)."
        ),
    )


class SignalEConfig(_SignalCommon):
    kind_filter: list[str] = Field(
        default_factory=lambda: ["domain_violation", "range_violation"]
    )


class SignalFConfig(_SignalCommon):
    # F-0037 / ISS-0028 — point-in-time firing mode. The trend test needs
    # >= mann_kendall_min_points completed cq-test runs, so a deployment with
    # one run got nothing. This gates an ALSO-emitted point-in-time mode over
    # the LATEST completed run; trend behavior is unchanged.
    point_in_time_enabled: bool = Field(
        default=True,
        description=(
            "Enable the point-in-time firing mode (F-0037/ISS-0028): fire on "
            "failing CQs in the latest completed cq-test run, without "
            "requiring a >=10-run failure-rate trend. Strength = failure "
            "rate of that run (D245 bounds)."
        ),
    )


class SignalPipelineConfig(BaseModel):
    """Root signal-pipeline configuration."""

    prometheus_url: str = "http://127.0.0.1:9090"
    mann_kendall_min_points: int = 10
    mann_kendall_alpha: float = 0.05

    signal_a: SignalAConfig = Field(default_factory=SignalAConfig)
    signal_b: SignalBConfig = Field(default_factory=SignalBConfig)
    signal_c: SignalCConfig = Field(default_factory=SignalCConfig)
    signal_d: SignalDConfig = Field(default_factory=SignalDConfig)
    signal_e: SignalEConfig = Field(default_factory=SignalEConfig)
    signal_f: SignalFConfig = Field(default_factory=SignalFConfig)


def load_config(path: str | Path | None = None) -> SignalPipelineConfig:
    """Load YAML config from ``path`` (default ``config/signal_pipeline.yaml``).

    Missing file returns defaults. ``yaml.safe_load`` returning ``None``
    (empty file) is treated identically.
    """
    p = Path(path) if path is not None else _DEFAULT_CONFIG_PATH
    if not p.exists():
        return SignalPipelineConfig()
    raw = yaml.safe_load(p.read_text()) or {}
    return SignalPipelineConfig.model_validate(raw)
