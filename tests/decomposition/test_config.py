"""CP3 config loader tests (Chunk 40).

Verifies YAML round-trip, ``DECOMPOSITION_`` env-var override path,
and that defaults match D277 §2.2 / D311 / D313 verbatim.
"""

from __future__ import annotations

import os

import pytest

from src.decomposition.config import DecompositionConfig, load_config


def test_config_yaml_round_trip(monkeypatch: pytest.MonkeyPatch):
    """Default YAML loads cleanly and ``model_dump`` round-trips."""
    for key in list(os.environ):
        if key.startswith("DECOMPOSITION_"):
            monkeypatch.delenv(key, raising=False)
    cfg = load_config()
    raw = cfg.model_dump()
    reloaded = DecompositionConfig(**raw)
    assert reloaded.umap.n_components == cfg.umap.n_components
    assert reloaded.layer3.leiden.seeds == cfg.layer3.leiden.seeds
    assert reloaded.layer2.outlier_ratio_gate == cfg.layer2.outlier_ratio_gate


def test_config_env_var_override(monkeypatch: pytest.MonkeyPatch):
    """``DECOMPOSITION_LAYER2__OUTLIER_RATIO_GATE`` overrides YAML value."""
    monkeypatch.setenv("DECOMPOSITION_LAYER2__OUTLIER_RATIO_GATE", "0.50")
    cfg = load_config()
    assert cfg.layer2.outlier_ratio_gate == 0.50


def test_config_defaults_match_d277_d311_d313_verbatim(monkeypatch: pytest.MonkeyPatch):
    """All locked defaults match the D-series spec verbatim."""
    for key in list(os.environ):
        if key.startswith("DECOMPOSITION_"):
            monkeypatch.delenv(key, raising=False)
    cfg = load_config()

    # D277 §2.2 — UMAP.
    assert cfg.umap.n_components == 10
    assert cfg.umap.n_neighbors == 15
    assert cfg.umap.min_dist == 0.1
    assert cfg.umap.metric == "cosine"
    assert cfg.umap.random_state == 42

    # Layer 2 outlier gate.
    assert cfg.layer2.outlier_ratio_gate == 0.30

    # D311 — NER.
    assert cfg.layer3.ner.model == "qwen2.5:7b-instruct"
    assert cfg.layer3.ner.concurrency == 4
    assert cfg.layer3.ner.per_10k_doc_budget_seconds == 7200

    # D313 — Leiden.
    assert cfg.layer3.leiden.seeds == [1, 2, 3, 4, 5]
    assert cfg.layer3.leiden.resolution == 1.0
    assert cfg.layer3.leiden.beta == 0.01
    assert cfg.layer3.leiden.n_iterations == 2
    assert cfg.layer3.ari_threshold == 0.6
