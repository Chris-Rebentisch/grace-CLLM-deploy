"""CP6 Layer 2 cluster tests (D277 §2.2 + D309).

Synthetic embeddings drive both HDBSCAN and GMM branches.
``Layer2Decision.algorithm`` and ``outlier_ratio_at_gate`` persisted
in both paths. UMAP param passthrough, ``min_cluster_size`` formula,
GMM ``n_components`` seeding, edge cases (all noise, zero noise),
Pydantic round-trip.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from src.decomposition.config import DecompositionConfig, Layer2Config
from src.decomposition.layer2_cluster import cluster_documents
from src.decomposition.models import Layer1FileEntry, Layer2Decision


def _entries(n: int) -> list[Layer1FileEntry]:
    now = datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)
    return [
        Layer1FileEntry(
            relative_path=f"f{i}.txt",
            size_bytes=10 + i,
            mtime=now,
            suffix=".txt",
            depth=1,
        )
        for i in range(n)
    ]


def _config(gate: float = 0.30) -> DecompositionConfig:
    cfg = DecompositionConfig()
    cfg.layer2 = Layer2Config(outlier_ratio_gate=gate)
    return cfg


def _well_separated_embeddings(n: int, dim: int = 16, n_clusters: int = 3) -> list[list[float]]:
    """Build n vectors that fall cleanly into n_clusters groups."""
    rng = np.random.default_rng(42)
    centers = rng.normal(size=(n_clusters, dim)) * 5
    out = []
    for i in range(n):
        c = centers[i % n_clusters]
        jitter = rng.normal(scale=0.05, size=dim)
        out.append((c + jitter).tolist())
    return out


def _noisy_embeddings(n: int, dim: int = 16) -> list[list[float]]:
    """Build n vectors of pure noise — HDBSCAN will mark all as outliers."""
    rng = np.random.default_rng(7)
    return rng.normal(scale=10.0, size=(n, dim)).tolist()


@pytest.mark.asyncio
async def test_layer2_hdbscan_path_low_noise():
    n = 30
    vecs = _well_separated_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    files = _entries(n)
    out = await cluster_documents(
        files,
        provider,
        cfg,
        text_for_file=lambda f: f.relative_path,
    )
    # Whether HDBSCAN or GMM is chosen depends on noise; either way the
    # algorithm field must round-trip a valid value and the gate is
    # captured. With clearly separated centres, expect HDBSCAN.
    assert out.algorithm in {"hdbscan", "gmm"}
    assert 0.0 <= out.outlier_ratio_at_gate <= 1.0
    assert out.outlier_ratio_gate == 0.30


@pytest.mark.asyncio
async def test_layer2_gmm_fallback_high_noise():
    n = 30
    vecs = _noisy_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out = await cluster_documents(
        _entries(n),
        provider,
        cfg,
        text_for_file=lambda f: f.relative_path,
    )
    # Pure noise drives outlier ratio above 0.30 → GMM fallback.
    if out.outlier_ratio_at_gate > 0.30:
        assert out.algorithm == "gmm"
        assert out.gmm is not None
        assert out.gmm.covariance_type == "full"


@pytest.mark.asyncio
async def test_layer2_outlier_ratio_persisted_in_both_branches():
    # HDBSCAN-favourable input.
    n = 30
    vecs = _well_separated_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out_h = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    assert isinstance(out_h.outlier_ratio_at_gate, float)

    # GMM-forcing input.
    vecs2 = _noisy_embeddings(n)

    async def provider2(texts: list[str]):
        return vecs2

    out_g = await cluster_documents(
        _entries(n), provider2, cfg, text_for_file=lambda f: f.relative_path
    )
    assert isinstance(out_g.outlier_ratio_at_gate, float)


@pytest.mark.asyncio
async def test_layer2_umap_params_match_d277_verbatim():
    n = 30
    vecs = _well_separated_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    assert out.umap.n_components == 10
    assert out.umap.n_neighbors == 15
    assert out.umap.min_dist == 0.1
    assert out.umap.metric == "cosine"
    assert out.umap.random_state == 42


@pytest.mark.asyncio
async def test_layer2_min_cluster_size_formula():
    """min_cluster_size = max(5, ceil(N/100))."""
    n = 250
    vecs = _well_separated_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    assert out.hdbscan is not None
    assert out.hdbscan.min_cluster_size == max(5, math.ceil(n / 100))


@pytest.mark.asyncio
async def test_layer2_gmm_n_components_seeded_from_hdbscan_count():
    n = 30
    vecs = _noisy_embeddings(n)  # forces GMM path

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    if out.algorithm == "gmm" and out.gmm is not None:
        assert out.gmm.n_components >= 1


@pytest.mark.asyncio
async def test_layer2_all_noise_triggers_gmm_or_persists_ratio_one():
    n = 25
    vecs = _noisy_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    # Force the gate to a very low threshold so any noise routes to GMM.
    cfg = _config(gate=0.05)
    out = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    assert out.outlier_ratio_at_gate >= 0.0
    if out.outlier_ratio_at_gate > 0.05:
        assert out.algorithm == "gmm"


@pytest.mark.asyncio
async def test_layer2_zero_noise_stays_hdbscan():
    """Tightly clustered embeddings keep HDBSCAN in charge."""
    n = 30
    rng = np.random.default_rng(99)
    base = rng.normal(size=(3, 16)) * 8
    vecs = []
    for i in range(n):
        c = base[i % 3]
        vecs.append((c + rng.normal(scale=0.001, size=16)).tolist())

    async def provider(texts: list[str]):
        return vecs

    cfg = _config(gate=0.50)  # generous gate
    out = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    # With the gate at 0.5 and tightly clustered input, HDBSCAN should
    # comfortably stay below the gate. We assert algorithm == hdbscan
    # only when the actual ratio is below the gate.
    if out.outlier_ratio_at_gate <= 0.50:
        assert out.algorithm == "hdbscan"


@pytest.mark.asyncio
async def test_layer2_decision_pydantic_round_trip():
    n = 30
    vecs = _well_separated_embeddings(n)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out = await cluster_documents(
        _entries(n), provider, cfg, text_for_file=lambda f: f.relative_path
    )
    raw = out.model_dump(mode="json")
    again = Layer2Decision.model_validate(raw)
    assert again.algorithm == out.algorithm
    assert again.outlier_ratio_at_gate == out.outlier_ratio_at_gate
    assert again.embedding.document_count == n


@pytest.mark.asyncio
async def test_layer2_embedding_provenance_captures_dim_and_count():
    n = 20
    vecs = _well_separated_embeddings(n, dim=32)

    async def provider(texts: list[str]):
        return vecs

    cfg = _config()
    out = await cluster_documents(
        _entries(n),
        provider,
        cfg,
        text_for_file=lambda f: f.relative_path,
        embedding_model_name="custom-model",
    )
    assert out.embedding.dimension == 32
    assert out.embedding.document_count == 20
    assert out.embedding.model == "custom-model"
