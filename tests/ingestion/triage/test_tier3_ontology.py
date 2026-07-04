"""Tier 3 ontology-similarity filter tests (Chunk 56 CP4 — 7 tests)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest
import structlog

from src.ingestion.communications.triage.config import Tier3Config
from src.ingestion.communications.triage.tier3_ontology import (
    build_ontology_embedding_matrix,
    run_tier3_batch,
)
from src.ingestion.models import CommunicationEvent


def _make_event(**overrides) -> CommunicationEvent:
    defaults = dict(
        source_id=uuid4(),
        message_id=f"<{uuid4()}@example.com>",
        sender_email="alice@example.com",
        body_plain="Test email body about legal entities",
        source_type="mbox",
    )
    defaults.update(overrides)
    return CommunicationEvent(**defaults)


@pytest.mark.asyncio
async def test_below_threshold_filtered():
    """Event below threshold is filtered."""
    config = Tier3Config(threshold=0.30)
    # Ontology embeddings: single description
    ontology_emb = (np.array([[1.0, 0.0, 0.0]]), ["Legal_Entity"])

    ev = _make_event(body_plain="Completely unrelated topic about cooking")

    with patch("src.ingestion.communications.triage.tier3_ontology.embed_texts", new_callable=AsyncMock) as mock_embed:
        # Return a very different vector
        mock_embed.return_value = [[0.0, 1.0, 0.0]]
        results = await run_tier3_batch([ev], ontology_emb, config, "http://localhost:11434")

    assert results[0] == "filtered_t3_below_threshold"


@pytest.mark.asyncio
async def test_at_threshold_passes():
    """Event at or above threshold passes."""
    config = Tier3Config(threshold=0.30)
    ontology_emb = (np.array([[1.0, 0.0, 0.0]]), ["Legal_Entity"])

    ev = _make_event(body_plain="Legal entity formation")

    with patch("src.ingestion.communications.triage.tier3_ontology.embed_texts", new_callable=AsyncMock) as mock_embed:
        # Return a vector with high similarity
        mock_embed.return_value = [[0.9, 0.1, 0.0]]
        results = await run_tier3_batch([ev], ontology_emb, config, "http://localhost:11434")

    assert results[0] is None  # Passed


@pytest.mark.asyncio
async def test_structlog_emitted_for_both(caplog):
    """structlog emits for both filtered AND passed events (D431)."""
    config = Tier3Config(threshold=0.50)
    ontology_emb = (np.array([[1.0, 0.0, 0.0]]), ["Legal_Entity"])

    ev_filtered = _make_event(body_plain="Cooking recipe")
    ev_passed = _make_event(body_plain="Legal entity")

    log_events: list[dict] = []

    def capture_log(logger, method_name, event_dict):
        log_events.append(event_dict)
        return event_dict

    with patch("src.ingestion.communications.triage.tier3_ontology.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.0, 1.0, 0.0], [0.95, 0.05, 0.0]]
        with structlog.testing.capture_logs() as cap_logs:
            results = await run_tier3_batch(
                [ev_filtered, ev_passed], ontology_emb, config, "http://localhost:11434"
            )

    tier3_logs = [e for e in cap_logs if e.get("event") == "triage_tier3_evaluated"]
    assert len(tier3_logs) == 2
    outcomes = {e["outcome"] for e in tier3_logs}
    assert "filtered" in outcomes
    assert "passed" in outcomes


@pytest.mark.asyncio
async def test_no_ontology_graceful_degradation():
    """No ontology → all events pass through."""
    config = Tier3Config(threshold=0.30)
    ontology_emb = (np.array([]), [])  # Empty

    ev = _make_event(body_plain="Anything")
    results = await run_tier3_batch([ev], ontology_emb, config, "http://localhost:11434")
    assert results[0] is None  # Pass-through


@pytest.mark.asyncio
async def test_build_ontology_returns_empty_on_none_version():
    """build_ontology_embedding_matrix returns empty on None active version."""
    db = MagicMock()
    with patch("src.ontology.database.get_active_version", return_value=None):
        matrix, labels = await build_ontology_embedding_matrix(db, "http://localhost:11434")
    assert matrix.size == 0
    assert labels == []


@pytest.mark.asyncio
async def test_batch_mixed_results():
    """Batch with mixed threshold results."""
    config = Tier3Config(threshold=0.50)
    ontology_emb = (np.array([[1.0, 0.0, 0.0]]), ["SomeType"])

    ev1 = _make_event(body_plain="Related")
    ev2 = _make_event(body_plain="Unrelated")

    with patch("src.ingestion.communications.triage.tier3_ontology.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.9, 0.1, 0.0], [0.0, 1.0, 0.0]]
        results = await run_tier3_batch([ev1, ev2], ontology_emb, config, "http://localhost:11434")

    assert results[0] is None  # Passed
    assert results[1] == "filtered_t3_below_threshold"


@pytest.mark.asyncio
async def test_threshold_from_config_honored():
    """Custom threshold from config is applied."""
    config = Tier3Config(threshold=0.90)  # Very high threshold
    ontology_emb = (np.array([[1.0, 0.0, 0.0]]), ["Type"])

    ev = _make_event(body_plain="Moderate match")

    with patch("src.ingestion.communications.triage.tier3_ontology.embed_texts", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = [[0.3, 0.9, 0.1]]  # cosine sim ~0.32 against [1,0,0] < 0.9 threshold
        results = await run_tier3_batch([ev], ontology_emb, config, "http://localhost:11434")

    assert results[0] == "filtered_t3_below_threshold"
