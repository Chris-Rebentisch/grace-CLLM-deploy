"""D264 idempotency regression — verify Chunk 67 changes preserve the
guarantee that two consecutive decay runs with the same observation_time
produce byte-identical extraction_confidence.

CP5 of Chunk 67 spec.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.extraction.confidence_decay import (
    DecayConfig,
    decay_confidence,
    decay_run,
)


def _make_config() -> DecayConfig:
    return DecayConfig(
        t_half_days=180.0,
        verdict_floors={"SUPPORTED": 0.5, "INSUFFICIENT": 0.5, "REFUTED": 0.05},
        default_confidence_at_verification=0.9,
        rows_decayed_equality_epsilon=1e-9,
    )


class TestByteIdenticalPostState:
    """Two decay runs with identical observation_time produce same extraction_confidence."""

    def test_byte_identical_post_state(self) -> None:
        config = _make_config()
        verified_at = datetime.now(timezone.utc) - timedelta(days=90)
        obs_time = datetime.now(timezone.utc)
        delta_days = (obs_time - verified_at).total_seconds() / 86400.0
        expected_c = decay_confidence(0.9, delta_days, 180.0, 0.5)

        # Track all persisted confidence values across both runs
        persisted_values: list[list[float]] = [[], []]

        def make_client(run_idx: int):
            """Create a mock arcade client that captures persisted values."""
            client = AsyncMock()
            # After first run, entity has the decayed value
            c_value = 0.9 if run_idx == 0 else expected_c

            async def fake_cypher(query, params=None, **kwargs):
                if "SET" in query:
                    if params and "c" in params:
                        persisted_values[run_idx].append(params["c"])
                    return {"result": []}
                elif "()-[r]->()" in query:
                    return {"result": []}
                else:
                    return {"result": [{
                        "n": {
                            "grace_id": "g-001",
                            "confidence_at_verification": 0.9,
                            "last_verified_at": verified_at.isoformat(),
                            "verdict": "SUPPORTED",
                            "extraction_confidence": c_value,
                            "ontology_module": "test",
                        }
                    }]}

            client.execute_cypher = AsyncMock(side_effect=fake_cypher)
            return client

        for run_idx in range(2):
            client = make_client(run_idx)
            with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
                 patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
                 patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
                asyncio.run(decay_run(
                    observation_time=obs_time,
                    config=config,
                    client=client,
                    dry_run=False,
                ))

        # Both runs persist the same extraction_confidence value
        assert len(persisted_values[0]) == 1
        assert len(persisted_values[1]) == 1
        assert persisted_values[0][0] == persisted_values[1][0]


class TestRowsActuallyMutatedZeroOnSecondRun:
    """rows_actually_mutated == 0 on the second run; rows_decayed > 0 on both."""

    def test_rows_actually_mutated_zero_on_second_run(self) -> None:
        config = _make_config()
        verified_at = datetime.now(timezone.utc) - timedelta(days=90)
        obs_time = datetime.now(timezone.utc)
        delta_days = (obs_time - verified_at).total_seconds() / 86400.0
        expected_c = decay_confidence(0.9, delta_days, 180.0, 0.5)

        results = []

        for run_idx in range(2):
            c_value = 0.9 if run_idx == 0 else expected_c
            client = AsyncMock()

            async def fake_cypher(query, params=None, _c=c_value, **kwargs):
                if "SET" in query:
                    return {"result": []}
                elif "()-[r]->()" in query:
                    return {"result": []}
                else:
                    return {"result": [{
                        "n": {
                            "grace_id": "g-001",
                            "confidence_at_verification": 0.9,
                            "last_verified_at": verified_at.isoformat(),
                            "verdict": "SUPPORTED",
                            "extraction_confidence": _c,
                            "ontology_module": "test",
                        }
                    }]}

            client.execute_cypher = AsyncMock(side_effect=fake_cypher)

            with patch("src.analytics.metrics.decay_batch_rows_processed", MagicMock()), \
                 patch("src.analytics.metrics.decay_batch_rows_actually_mutated", MagicMock()), \
                 patch("src.analytics.metrics.decay_batch_duration", MagicMock()):
                result = asyncio.run(decay_run(
                    observation_time=obs_time,
                    config=config,
                    client=client,
                    dry_run=False,
                ))
            results.append(result)

        # Both runs decayed (legacy counter semantics unchanged)
        assert results[0].rows_decayed > 0
        assert results[1].rows_decayed > 0
        # First run actually mutated; second run did not
        assert results[0].rows_actually_mutated > 0
        assert results[1].rows_actually_mutated == 0
