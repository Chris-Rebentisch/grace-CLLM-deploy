"""D453 — Honest mutation counter tests for the decay batch.

Tests ``DecayResult.rows_actually_mutated`` and the epsilon comparison
logic added in Chunk 67. Also validates ``rows_skipped_no_verification_metadata``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.confidence_decay import (
    DecayConfig,
    DecayResult,
    decay_run,
)


def _make_config(**overrides) -> DecayConfig:
    defaults = {
        "t_half_days": 180.0,
        "verdict_floors": {"SUPPORTED": 0.5, "INSUFFICIENT": 0.5, "REFUTED": 0.05},
        "default_confidence_at_verification": 0.9,
        "rows_decayed_equality_epsilon": 1e-9,
    }
    defaults.update(overrides)
    return DecayConfig(**defaults)


def _mock_arcade_with_entities(entities: list[dict], edges: list[dict] | None = None):
    """Return a mock ArcadeClient that returns the given entities/edges."""
    client = AsyncMock()

    async def fake_cypher(query, **kwargs):
        if "()-[r]->()" in query and "SET" not in query:
            return {"result": [{
                "r": e
            } for e in (edges or [])]}
        elif "SET" in query:
            return {"result": []}
        else:
            return {"result": [{
                "n": e
            } for e in entities]}

    client.execute_cypher = AsyncMock(side_effect=fake_cypher)
    return client


class TestFirstRunPositiveRowsActuallyMutated:
    """First decay on stamped entity produces rows_actually_mutated > 0."""

    def test_first_run_positive_rows_actually_mutated(self) -> None:
        config = _make_config()
        verified_at = datetime.now(timezone.utc) - timedelta(days=90)
        entities = [{
            "grace_id": "g-001",
            "confidence_at_verification": 0.9,
            "last_verified_at": verified_at.isoformat(),
            "verdict": "SUPPORTED",
            "extraction_confidence": 0.9,
            "ontology_module": "test",
        }]
        client = _mock_arcade_with_entities(entities)
        obs_time = datetime.now(timezone.utc)

        with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
            result = asyncio.run(decay_run(
                observation_time=obs_time,
                config=config,
                client=client,
                dry_run=False,
            ))

        assert result.rows_actually_mutated > 0
        assert result.rows_decayed > 0


class TestIdempotentZero:
    """Second run with same observation-time produces rows_actually_mutated == 0."""

    def test_idempotent_zero(self) -> None:
        config = _make_config()
        verified_at = datetime.now(timezone.utc) - timedelta(days=90)
        obs_time = datetime.now(timezone.utc)

        # Compute what the decayed value would be
        from src.extraction.confidence_decay import decay_confidence
        delta_days = (obs_time - verified_at).total_seconds() / 86400.0
        expected_c = decay_confidence(0.9, delta_days, 180.0, 0.5)

        # Entity already has the decayed value as extraction_confidence
        entities = [{
            "grace_id": "g-001",
            "confidence_at_verification": 0.9,
            "last_verified_at": verified_at.isoformat(),
            "verdict": "SUPPORTED",
            "extraction_confidence": expected_c,
            "ontology_module": "test",
        }]
        client = _mock_arcade_with_entities(entities)

        with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
            result = asyncio.run(decay_run(
                observation_time=obs_time,
                config=config,
                client=client,
                dry_run=False,
            ))

        assert result.rows_actually_mutated == 0
        assert result.rows_decayed > 0  # legacy counter still fires


class TestEpsilonBoundary:
    """Epsilon boundary: abs(new_c - old_c) <= 1e-9 does NOT increment."""

    def test_epsilon_boundary(self) -> None:
        config = _make_config(rows_decayed_equality_epsilon=1e-9)
        verified_at = datetime.now(timezone.utc) - timedelta(days=90)
        obs_time = datetime.now(timezone.utc)

        from src.extraction.confidence_decay import decay_confidence
        delta_days = (obs_time - verified_at).total_seconds() / 86400.0
        new_c = decay_confidence(0.9, delta_days, 180.0, 0.5)

        # Case 1: old_c == new_c exactly -> should NOT increment
        entities_exact = [{
            "grace_id": "g-exact",
            "confidence_at_verification": 0.9,
            "last_verified_at": verified_at.isoformat(),
            "verdict": "SUPPORTED",
            "extraction_confidence": new_c,
            "ontology_module": "test",
        }]
        client = _mock_arcade_with_entities(entities_exact)

        with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
            result_exact = asyncio.run(decay_run(
                observation_time=obs_time,
                config=config,
                client=client,
                dry_run=False,
            ))

        assert result_exact.rows_actually_mutated == 0

        # Case 2: old_c differs by more than epsilon -> should increment
        entities_diff = [{
            "grace_id": "g-diff",
            "confidence_at_verification": 0.9,
            "last_verified_at": verified_at.isoformat(),
            "verdict": "SUPPORTED",
            "extraction_confidence": new_c + 0.01,  # differs by 0.01 >> epsilon
            "ontology_module": "test",
        }]
        client2 = _mock_arcade_with_entities(entities_diff)

        with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
            result_diff = asyncio.run(decay_run(
                observation_time=obs_time,
                config=config,
                client=client2,
                dry_run=False,
            ))

        assert result_diff.rows_actually_mutated > 0


class TestRowsSkippedNoVerificationMetadata:
    """Entity without stamps produces rows_skipped_no_verification_metadata > 0."""

    def test_rows_skipped_no_verification_metadata(self) -> None:
        config = _make_config()
        # Entity missing all three verification properties
        entities = [{
            "grace_id": "g-no-stamps",
            "extraction_confidence": 0.8,
            "ontology_module": "test",
        }]
        client = _mock_arcade_with_entities(entities)
        obs_time = datetime.now(timezone.utc)

        with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
             patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
            result = asyncio.run(decay_run(
                observation_time=obs_time,
                config=config,
                client=client,
                dry_run=False,
            ))

        assert result.rows_skipped_no_verification_metadata > 0
        assert result.rows_skipped > 0
        assert result.rows_decayed == 0
