"""Tier 3 ontology-similarity filter — cosine similarity via D265 embeddings (Chunk 56, D431).

Default threshold 0.30. structlog emission for both filtered and passed events.
Graceful degradation when no ontology exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import structlog

from src.shared.embeddings import cosine_similarity, embed_texts

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from src.ingestion.communications.triage.config import Tier3Config
    from src.ingestion.models import CommunicationEvent

logger = structlog.get_logger()


async def build_ontology_embedding_matrix(
    db: Session,
    ollama_base_url: str,
    ontology_module: str | None = None,
    model: str = "nomic-embed-text",
) -> tuple[np.ndarray, list[str]]:
    """Build embedding matrix from active ontology descriptions.

    Returns ``(embedding_matrix, description_labels)``.
    No-ontology graceful degradation: returns ``(empty_array, [])`` if no
    active version or zero descriptions.
    """
    from src.ontology.database import get_active_version

    version = get_active_version(db, segment_id=None)
    if version is None:
        return np.array([]), []

    schema_json = version.schema_json if hasattr(version, "schema_json") else {}
    if not schema_json:
        return np.array([]), []

    descriptions: list[str] = []
    labels: list[str] = []

    # Walk JSON Schema for entity and relationship descriptions
    defs = schema_json.get("$defs", schema_json.get("definitions", {}))
    if isinstance(defs, dict):
        for type_name, type_def in defs.items():
            desc = type_def.get("description", "")
            if desc:
                descriptions.append(desc)
                labels.append(type_name)
            # Also walk properties for relationship descriptions
            props = type_def.get("properties", {})
            if isinstance(props, dict):
                for prop_name, prop_def in props.items():
                    prop_desc = prop_def.get("description", "")
                    if prop_desc:
                        descriptions.append(prop_desc)
                        labels.append(f"{type_name}.{prop_name}")

    if not descriptions:
        return np.array([]), []

    embeddings = await embed_texts(descriptions, base_url=ollama_base_url, model=model)
    return np.array(embeddings), labels


async def run_tier3_batch(
    events: list[CommunicationEvent],
    ontology_embeddings: tuple[np.ndarray, list[str]],
    config: Tier3Config,
    ollama_base_url: str,
) -> list[str | None]:
    """Evaluate a batch of events against the ontology embedding matrix.

    Returns a list parallel to ``events``: outcome label or None (pass-through).
    """
    matrix, labels = ontology_embeddings

    # No-ontology graceful degradation
    if matrix.size == 0:
        logger.warning("triage_tier3_no_ontology")
        return [None] * len(events)

    # Build event texts
    texts: list[str] = []
    for ev in events:
        text = ev.body_plain or ev.subject or ""
        texts.append(text)

    if not texts:
        return [None] * len(events)

    event_embeddings = await embed_texts(texts, base_url=ollama_base_url, model="nomic-embed-text")
    event_matrix = np.array(event_embeddings)

    results: list[str | None] = []
    for i, ev in enumerate(events):
        if event_matrix.ndim < 2 or i >= event_matrix.shape[0]:
            results.append(None)
            continue

        # Compute cosine similarity against all ontology descriptions
        sims = cosine_similarity(event_matrix[i], matrix)
        max_sim = float(np.max(sims)) if sims.size > 0 else 0.0

        if max_sim < config.threshold:
            outcome = "filtered_t3_below_threshold"
            results.append(outcome)
        else:
            outcome = None
            results.append(None)

        logger.info(
            "triage_tier3_evaluated",
            message_id=ev.message_id,
            max_similarity=round(max_sim, 4),
            outcome="filtered" if outcome else "passed",
        )

    return results
