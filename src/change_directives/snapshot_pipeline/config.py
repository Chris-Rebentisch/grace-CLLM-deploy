"""YAML-backed configuration for the snapshot pipeline (Chunk 39, D301)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_GRACE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = _GRACE_ROOT / "config" / "snapshot_pipeline.yaml"


@dataclass
class SnapshotPipelineConfig:
    velocity_window_days: int = 30
    stalled_epsilon: float = 0.005
    stalled_min_age_days: int = 7
    sample_id_cap: int = 10


def load_snapshot_config(path: Path | None = None) -> SnapshotPipelineConfig:
    p = path or DEFAULT_CONFIG_PATH
    if not p.exists():
        return SnapshotPipelineConfig()
    raw = yaml.safe_load(p.read_text()) or {}
    return SnapshotPipelineConfig(
        velocity_window_days=int(raw.get("velocity_window_days", 30)),
        stalled_epsilon=float(raw.get("stalled_epsilon", 0.005)),
        stalled_min_age_days=int(raw.get("stalled_min_age_days", 7)),
        sample_id_cap=int(raw.get("sample_id_cap", 10)),
    )
