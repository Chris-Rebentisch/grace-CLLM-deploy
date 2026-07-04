"""Tier 1: embedding generation, similarity computation, and HDBSCAN clustering."""

import hdbscan
import httpx
import numpy as np
import structlog
from sklearn.metrics.pairwise import cosine_similarity

from src.discovery.cq_models import CompetencyQuestion
from src.discovery.merge_models import HDBSCANResult, Tier1Result
from src.shared.config import get_settings

logger = structlog.get_logger()


async def embed_texts(
    texts: list[str], model: str = "nomic-embed-text"
) -> list[list[float]]:
    """Generate embeddings for a list of texts via Ollama /api/embed.

    Posts to Ollama's embedding endpoint with retry logic.
    Returns a list of 384-dimensional float vectors.
    """
    settings = get_settings()
    base_url = settings.ollama_base_url
    url = f"{base_url}/api/embed"
    payload = {"model": model, "input": texts}

    last_error = None
    for attempt in range(3):  # 1 initial + 2 retries
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                embeddings = data.get("embeddings", [])
                logger.info(
                    "embed_texts_complete",
                    count=len(texts),
                    embedding_dim=len(embeddings[0]) if embeddings else 0,
                    attempt=attempt + 1,
                )
                return embeddings
        except httpx.TimeoutException:
            last_error = "Request timed out"
            logger.warning(
                "embed_texts_timeout", attempt=attempt + 1, timeout=60
            )
        except httpx.ConnectError as e:
            last_error = f"Connection error: {e}"
            logger.warning(
                "embed_texts_connect_error", attempt=attempt + 1, error=str(e)
            )
        except httpx.HTTPStatusError as e:
            last_error = f"HTTP {e.response.status_code}: {e.response.text}"
            logger.warning(
                "embed_texts_http_error",
                attempt=attempt + 1,
                status_code=e.response.status_code,
            )

    raise RuntimeError(
        f"Embedding request failed after 3 attempts: {last_error}"
    )


def compute_similarity_matrix(embeddings: list[list[float]]) -> np.ndarray:
    """Compute N x N cosine similarity matrix from embedding vectors.

    Returns a numpy array where element [i][j] is the cosine similarity
    between embeddings[i] and embeddings[j].
    """
    arr = np.array(embeddings)
    sim_matrix = cosine_similarity(arr)
    logger.info(
        "similarity_matrix_computed",
        shape=sim_matrix.shape,
        mean_similarity=float(np.mean(sim_matrix)),
    )
    return sim_matrix


def cluster_embeddings(
    embeddings: list[list[float]], config: dict
) -> HDBSCANResult:
    """Run HDBSCAN clustering on embedding vectors using precomputed cosine distance.

    Config keys:
      - min_cluster_size (default 2)
      - cluster_selection_method (default "eom")
    """
    sim_matrix = compute_similarity_matrix(embeddings)
    distance_matrix = 1.0 - sim_matrix

    # Ensure non-negative distances (floating point can produce tiny negatives)
    distance_matrix = np.clip(distance_matrix, 0.0, None)

    min_cluster_size = config.get("min_cluster_size", 2)
    cluster_selection_method = config.get("cluster_selection_method", "eom")

    clusterer = hdbscan.HDBSCAN(
        metric="precomputed",
        min_cluster_size=min_cluster_size,
        cluster_selection_method=cluster_selection_method,
        prediction_data=True,
    )
    clusterer.fit(distance_matrix)

    labels = clusterer.labels_.tolist()
    probabilities = clusterer.probabilities_.tolist()

    # Extract cluster persistence
    cluster_persistence: dict[int, float] = {}
    if hasattr(clusterer, "cluster_persistence_") and clusterer.cluster_persistence_ is not None:
        unique_labels = sorted(set(l for l in labels if l >= 0))
        for i, label in enumerate(unique_labels):
            if i < len(clusterer.cluster_persistence_):
                cluster_persistence[label] = float(clusterer.cluster_persistence_[i])

    # Try soft memberships
    soft_memberships: list[list[float]] = []
    try:
        membership_vectors = hdbscan.all_points_membership_vectors(clusterer)
        soft_memberships = membership_vectors.tolist()
    except Exception as e:
        logger.warning(
            "soft_membership_unavailable",
            reason=str(e),
            msg="Falling back to empty soft memberships",
        )

    n_clusters = len(set(l for l in labels if l >= 0))
    n_noise = labels.count(-1)

    logger.info(
        "hdbscan_complete",
        n_clusters=n_clusters,
        n_noise=n_noise,
        total_points=len(labels),
        min_cluster_size=min_cluster_size,
    )

    return HDBSCANResult(
        labels=labels,
        probabilities=probabilities,
        cluster_persistence=cluster_persistence,
        soft_memberships=soft_memberships,
        n_clusters=n_clusters,
        n_noise=n_noise,
    )


async def run_tier1(
    cqs: list[CompetencyQuestion], config: dict
) -> Tier1Result:
    """Execute the full Tier 1 pipeline: embed, compute similarity, cluster.

    Args:
        cqs: List of competency questions to cluster.
        config: Configuration dict (cq_merge section from discovery.yaml).

    Returns:
        Tier1Result with embeddings, similarity matrix, clustering, and group mappings.
    """
    texts = [cq.canonical_text for cq in cqs]
    logger.info("tier1_start", cq_count=len(texts))

    # Step 1: Embed
    embeddings = await embed_texts(texts)

    # Step 2: Similarity matrix
    sim_matrix = compute_similarity_matrix(embeddings)

    # Step 3: Cluster
    hdbscan_result = cluster_embeddings(embeddings, config)

    # Step 4: Build cluster groups and singleton indices
    cluster_groups: dict[int, list[int]] = {}
    singleton_indices: list[int] = []

    for idx, label in enumerate(hdbscan_result.labels):
        if label == -1:
            singleton_indices.append(idx)
        else:
            if label not in cluster_groups:
                cluster_groups[label] = []
            cluster_groups[label].append(idx)

    logger.info(
        "tier1_complete",
        n_clusters=len(cluster_groups),
        n_singletons=len(singleton_indices),
        total_cqs=len(cqs),
    )

    return Tier1Result(
        embeddings=[e for e in embeddings],
        similarity_matrix=sim_matrix.tolist(),
        hdbscan_result=hdbscan_result,
        cluster_groups={k: v for k, v in cluster_groups.items()},
        singleton_indices=singleton_indices,
    )
