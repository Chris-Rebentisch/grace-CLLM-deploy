"""Layer 2 — content clustering via embed → UMAP → HDBSCAN/GMM (D277 §2.2 + D309).

The pipeline:

1. Per-document text = title + first-500-words (D309) is embedded via
   ``src.shared.embeddings.embed_texts()``. The embedding provider is
   the contract surface; tests patch it.
2. Embeddings are projected to ``UmapParams`` (D277 verbatim:
   ``n_components=10, n_neighbors=15, min_dist=0.1, metric='cosine',
   random_state=42``).
3. HDBSCAN with ``min_cluster_size=max(5, ceil(N/100))`` produces a
   first clustering. Outlier ratio = ``noise_count / N``.
4. If ``outlier_ratio > config.layer2.outlier_ratio_gate`` (default
   0.30), a Gaussian mixture model fallback runs with
   ``n_components`` seeded from the HDBSCAN non-noise cluster count
   (clamped to ``≥1``) and ``covariance_type='full'``.
5. ``Layer2Decision.algorithm`` records the chosen branch;
   ``outlier_ratio_at_gate`` is persisted in both branches so future
   re-tuning can read calibration signal off the artifact.

The text-extraction step is delegated through a
``per_document_text`` function so callers (tests, orchestrator) can
short-circuit Docling.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import numpy as np

from src.decomposition.config import DecompositionConfig
from src.decomposition.models import (
    EmbeddingProvenance,
    GmmParams,
    HdbscanParams,
    Layer1FileEntry,
    Layer2Decision,
    UmapParams,
)


EmbeddingProvider = Callable[[list[str]], Awaitable[list[list[float]]]]


def _default_text_for_file(archive_root: Path, entry: Layer1FileEntry) -> str:
    """Default text extraction. Tests usually inject their own.

    Imports ``extract_text`` lazily so this module stays import-light
    for environments that don't ship Docling.
    """
    from src.decomposition.text_extractor import extract_text

    full = archive_root / entry.relative_path
    res = extract_text(full)
    if res.skipped or res.body is None:
        return ""
    title = res.title or ""
    return f"{title}\n{res.body}".strip()


async def cluster_documents(
    file_inventory: list[Layer1FileEntry],
    embedding_provider: EmbeddingProvider,
    config: DecompositionConfig,
    *,
    archive_root: Path | None = None,
    text_for_file: Callable[[Layer1FileEntry], str] | None = None,
    embedding_model_name: str = "nomic-embed-text",
) -> Layer2Decision:
    """Cluster ``file_inventory`` and return a ``Layer2Decision``.

    ``embedding_provider`` is an async callable returning a list of
    vectors aligned with the input texts. ``text_for_file`` overrides
    the per-file text source (tests patch this; default uses the
    Docling-backed text extractor).
    """
    if archive_root is None and text_for_file is None:
        # The default extractor needs an archive_root to resolve
        # relative paths. Tests must pass either argument.
        raise ValueError(
            "cluster_documents requires either archive_root or text_for_file"
        )
    if text_for_file is None:
        assert archive_root is not None
        text_for_file = lambda entry: _default_text_for_file(archive_root, entry)

    texts = [text_for_file(entry) for entry in file_inventory]
    n_docs = len(texts)

    embeddings_list = await embedding_provider(texts)
    embeddings = np.asarray(embeddings_list, dtype=np.float64)
    if embeddings.ndim != 2 or embeddings.shape[0] != n_docs:
        raise ValueError(
            f"embedding_provider returned shape {embeddings.shape}; "
            f"expected ({n_docs}, D)"
        )

    umap_params = UmapParams(
        n_components=config.umap.n_components,
        n_neighbors=config.umap.n_neighbors,
        min_dist=config.umap.min_dist,
        metric=config.umap.metric,
        random_state=config.umap.random_state,
    )

    # UMAP requires n_neighbors < n_samples and at least a few rows to
    # produce a stable manifold. Tiny test fixtures (n<5) are projected
    # by truncation to keep the pipeline runnable in CI without UMAP
    # collapsing.
    if n_docs >= max(umap_params.n_neighbors + 1, 4):
        import umap  # type: ignore

        reducer = umap.UMAP(
            n_components=umap_params.n_components,
            n_neighbors=min(umap_params.n_neighbors, max(2, n_docs - 1)),
            min_dist=umap_params.min_dist,
            metric=umap_params.metric,
            random_state=umap_params.random_state,
        )
        reduced = reducer.fit_transform(embeddings)
    else:
        reduced = embeddings  # too small for meaningful UMAP

    min_cluster_size = max(5, math.ceil(n_docs / 100))
    hdbscan_params = HdbscanParams(min_cluster_size=min_cluster_size)

    import hdbscan  # type: ignore

    if n_docs >= min_cluster_size:
        clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size)
        labels = clusterer.fit_predict(reduced)
    else:
        labels = np.full(n_docs, -1, dtype=np.int64)

    labels_arr = np.asarray(labels, dtype=np.int64)
    noise_count = int(np.sum(labels_arr == -1))
    outlier_ratio = (noise_count / n_docs) if n_docs > 0 else 1.0

    hdbscan_cluster_count = int(
        len({int(l) for l in labels_arr.tolist() if int(l) != -1})
    )

    gate = config.layer2.outlier_ratio_gate
    use_gmm = outlier_ratio > gate

    final_algorithm = "hdbscan"
    final_labels = labels_arr.tolist()
    gmm_block: GmmParams | None = None

    if use_gmm and n_docs > 0:
        from sklearn.mixture import GaussianMixture  # type: ignore

        n_components = max(1, hdbscan_cluster_count)
        gmm = GaussianMixture(
            n_components=n_components,
            covariance_type="full",
            random_state=umap_params.random_state,
        )
        try:
            gmm.fit(reduced)
            gmm_labels = gmm.predict(reduced).tolist()
            final_algorithm = "gmm"
            final_labels = [int(x) for x in gmm_labels]
            gmm_block = GmmParams(n_components=n_components, covariance_type="full")
        except Exception:  # noqa: BLE001 — fallback fall-through
            # Stick with HDBSCAN labels if GMM degenerates.
            gmm_block = None

    cluster_count = len({int(l) for l in final_labels if int(l) != -1})
    outlier_count = sum(1 for l in final_labels if int(l) == -1)

    embedding_dim = int(embeddings.shape[1]) if embeddings.size else 0

    return Layer2Decision(
        algorithm=final_algorithm,
        cluster_count=cluster_count,
        outlier_count=outlier_count,
        outlier_ratio_at_gate=outlier_ratio,
        outlier_ratio_gate=gate,
        cluster_labels=[int(x) for x in final_labels],
        umap=umap_params,
        hdbscan=hdbscan_params,
        gmm=gmm_block,
        embedding=EmbeddingProvenance(
            model=embedding_model_name,
            dimension=embedding_dim,
            document_count=n_docs,
        ),
    )
