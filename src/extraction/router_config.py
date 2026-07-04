"""D471 extraction router configuration models.

Pydantic v2 models for the provider catalog and router configuration.
Loads from ``config/extraction_router.yaml``.

Authorization: D471 — Chunk 72b.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ProviderProfile(BaseModel):
    """A single provider entry in the extraction router catalog.

    Layer A = static caps, Layer B = pricing, Layer C = throughput (optional),
    Layer E = operational posture.
    """

    # Layer A — static caps
    context_window: int = Field(description="Maximum input context window in tokens")
    max_output_tokens: int = Field(description="Maximum output tokens per request")

    # Layer B — pricing
    pricing_input_per_m: float = Field(description="Cost per million input tokens (USD)")
    pricing_output_per_m: float = Field(description="Cost per million output tokens (USD)")
    batch_discount: float = Field(default=0.0, description="Fractional discount for batch API (0.0–1.0)")
    cache_hit_price: float | None = Field(default=None, description="Cost per million cached-input tokens (USD), if supported")

    # Layer E — operational posture
    airgap_eligible: bool = Field(description="Whether this provider can run fully airgapped (local)")
    data_residency: str = Field(default="any", description="Data residency constraint (e.g. 'any', 'us', 'eu')")

    # Layer C — throughput (optional)
    typical_throughput_tok_s: float | None = Field(default=None, description="Typical throughput in tokens/second (informational)")

    # D502 (Chunk 77b): vision capability fields
    vision_capable: bool = Field(default=False, description="Whether this provider supports vision/image input")
    vision_model: str | None = Field(default=None, description="Vision model identifier (e.g. 'qwen2.5-vl:32b')")


class ExtractionShard(BaseModel):
    """A group of source files routed to a single provider/model pair."""

    source_paths: list[Path] = Field(description="Files assigned to this shard")
    provider: str = Field(description="Provider name (key in profiles dict)")
    model: str = Field(description="Model identifier for this shard")
    estimated_input_tokens: int = Field(description="Estimated total input tokens for this shard")


class RouterConfig(BaseModel):
    """Top-level router configuration loaded from extraction_router.yaml."""

    profiles: dict[str, ProviderProfile] = Field(description="Provider catalog keyed by profile name")
    source_path_allowlist: list[str] = Field(default_factory=list, description="Allowed source path prefixes")
    alpha_cost: float = Field(default=1.0, description="Cost weight for multi-objective scoring")  # 72c: enables hybrid scoring
    beta_time: float = Field(default=0.0, description="Time weight for multi-objective scoring")  # 72c: enables hybrid scoring
    gamma_quality: float = Field(default=0.0, description="Quality weight for multi-objective scoring")  # 72c: enables hybrid scoring
    exploration_epsilon: float = Field(default=0.0, description="Epsilon for e-greedy exploration")  # 72c: enables hybrid scoring
    pricing_snapshot_date: str | None = Field(default=None, description="ISO date when pricing was last verified")
    size_tier_mapping: dict[str, str] = Field(default_factory=dict, description="Size tier to profile name mapping (small/medium/large)")


def load_router_config(path: Path | None = None) -> RouterConfig:
    """Load router configuration from YAML.

    Defaults to ``config/extraction_router.yaml`` resolved relative to the
    repository root (two parents up from this file).
    """
    if path is None:
        path = Path(__file__).resolve().parents[2] / "config" / "extraction_router.yaml"
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    return RouterConfig.model_validate(data)
