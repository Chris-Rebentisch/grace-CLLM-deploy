"""Decomposition pipeline configuration loader (Chunk 40).

Reads ``config/decomposition.yaml`` and overlays environment-variable
overrides via the ``DECOMPOSITION_`` prefix (pydantic-settings nested
delimiter ``__``). Defaults are pinned to D277 §2.2 (UMAP), D311 (NER),
and D313 (Leiden) verbatim.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


_DEFAULT_CONFIG_PATH = Path("config/decomposition.yaml")


class UmapConfig(BaseModel):
    """UMAP knobs — D277 §2.2 verbatim. Do not deviate without a D-amendment."""

    n_components: int = 10
    n_neighbors: int = 15
    min_dist: float = 0.1
    metric: str = "cosine"
    random_state: int = 42


class Layer1Config(BaseModel):
    max_depth: int | None = None
    exclude_hidden: bool = True


class Layer2Config(BaseModel):
    outlier_ratio_gate: float = 0.30


class NerConfig(BaseModel):
    model: str = "qwen2.5:7b-instruct"
    concurrency: int = Field(default=4, ge=1)
    per_10k_doc_budget_seconds: int = Field(default=7200, ge=1)


class LeidenConfig(BaseModel):
    seeds: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    resolution: float = 1.0
    beta: float = 0.01
    n_iterations: int = 2


class Layer3Config(BaseModel):
    ner: NerConfig = Field(default_factory=NerConfig)
    leiden: LeidenConfig = Field(default_factory=LeidenConfig)
    ari_threshold: float = 0.6


class Layer5Config(BaseModel):
    """Chunk 41 D320 — Layer 5 Structured Interview knobs.

    ``reformulation_pass_cap`` bounds the number of
    ``reject_all_reformulate`` decisions in a single lineage chain
    (default 1; outline Q1 default).
    """

    reformulation_pass_cap: int = Field(default=1, ge=0)


class Layer6SampleCQConfig(BaseModel):
    """Chunk 41 D324 — Layer 6 sample-CQ adapter knobs."""

    n: int = Field(default=5, ge=1)
    surface_cq_type: bool = True


class Layer6Config(BaseModel):
    sample_cqs: Layer6SampleCQConfig = Field(default_factory=Layer6SampleCQConfig)


class DecompositionConfig(BaseSettings):
    """Root decomposition configuration. Operator overrides via env vars.

    Env-var convention: ``DECOMPOSITION_<SECTION>__<FIELD>``. Nested
    fields use the double-underscore delimiter (pydantic-settings).
    Example: ``DECOMPOSITION_LAYER2__OUTLIER_RATIO_GATE=0.50``.
    """

    model_config = SettingsConfigDict(
        env_prefix="DECOMPOSITION_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    umap: UmapConfig = Field(default_factory=UmapConfig)
    layer1: Layer1Config = Field(default_factory=Layer1Config)
    layer2: Layer2Config = Field(default_factory=Layer2Config)
    layer3: Layer3Config = Field(default_factory=Layer3Config)
    layer5: Layer5Config = Field(default_factory=Layer5Config)
    layer6: Layer6Config = Field(default_factory=Layer6Config)


def _set_nested(target: dict, path: list[str], value: object) -> None:
    cur = target
    for key in path[:-1]:
        cur = cur.setdefault(key, {})
        if not isinstance(cur, dict):
            return
    cur[path[-1]] = value


def load_config(path: str | Path | None = None) -> DecompositionConfig:
    """Load YAML config from ``path`` (default ``config/decomposition.yaml``).

    Environment variables under the ``DECOMPOSITION_`` prefix override
    YAML values for the same field. Missing file returns the
    default-valued config (overlaid by env vars). Nested overrides use
    ``__`` as the section delimiter (pydantic-settings convention),
    e.g. ``DECOMPOSITION_LAYER2__OUTLIER_RATIO_GATE=0.50``.
    """
    p = Path(path) if path is not None else _DEFAULT_CONFIG_PATH

    raw: dict = {}
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}

    # Overlay env-var values on top of YAML so env wins (pydantic-settings
    # default precedence is init-kwargs > env, which would otherwise
    # invert the operator's intent).
    import os

    for key, value in os.environ.items():
        if not key.startswith("DECOMPOSITION_"):
            continue
        suffix = key[len("DECOMPOSITION_") :].lower()
        if not suffix:
            continue
        path_parts = suffix.split("__")
        _set_nested(raw, path_parts, value)

    return DecompositionConfig.model_validate(raw)
