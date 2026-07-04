"""Signal D detector tests (D245)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.analytics.signal_pipeline.config import SignalDConfig, SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_d import SignalDDetector


def _insert_claim_at(session, *, module: str, entity_type: str, day_offset: int):
    """Insert one claim dated day_offset days ago."""
    when = datetime.now(UTC) - timedelta(days=day_offset)
    session.execute(
        text(
            """
            INSERT INTO extraction_claims (
                claim_id, extraction_unit_id, entity_type, relationship_type,
                subject_name, predicate, object_name, properties_json,
                evidence_spans, status, decision_source, source_document_id,
                source_chunk_id, ontology_module, schema_version, created_at,
                claim_fingerprint
            ) VALUES (
                gen_random_uuid(), :unit, :et, NULL,
                :sn, 'is_a', NULL, '{}'::jsonb,
                '[]'::jsonb, 'auto_accepted', 'auto', 'doc1',
                'chunkX', :module, 1, :when,
                :fp
            )
            """
        ),
        {
            "unit": str(uuid4())[:24],
            "et": entity_type,
            "sn": f"{entity_type}-{uuid4().hex[:6]}",
            "module": module,
            "when": when,
            "fp": str(uuid4()),
        },
    )


@pytest.fixture
def seed_signal_d(test_engine):
    """Strictly decreasing daily counts over 12 days."""
    module = "signal_d_test"
    with test_engine.begin() as conn:
        # Older days have many rows; newer days have few. Sorted by
        # day ASC (oldest first), counts strictly decrease toward
        # today → trend == "decreasing".
        for day in range(12):
            day_offset = 11 - day  # 11 (oldest) ... 0 (today)
            n_rows = day_offset + 1  # 12, 11, ..., 1
            for _ in range(n_rows):
                _insert_claim_at(
                    conn, module=module, entity_type="DyingType",
                    day_offset=day_offset,
                )
    yield module
    with test_engine.begin() as conn:
        conn.execute(
            text("DELETE FROM extraction_claims WHERE ontology_module = :m"),
            {"m": module},
        )


@pytest.mark.asyncio
async def test_signal_d_fires_on_decreasing_trend(signal_run_context, seed_signal_d):
    cfg = SignalPipelineConfig()
    ctx = signal_run_context(
        config=cfg, target_ontology_modules=[seed_signal_d]
    )
    detector = SignalDDetector()
    records = await detector.detect(ctx)
    assert records, "expected at least one record for decreasing trend"
    rec = records[0]
    assert rec.signal_type == "D"
    assert rec.strength > 0.0
    assert rec.evidence_snapshot["trend"] == "decreasing"


@pytest.mark.asyncio
async def test_signal_d_skips_when_below_min_points(
    signal_run_context, test_engine
):
    """Series shorter than mann_kendall_min_points → no record."""
    module = "signal_d_short"
    with test_engine.begin() as conn:
        for day in range(3):  # only 3 days < min_points=10
            _insert_claim_at(
                conn, module=module, entity_type="ShortType", day_offset=day
            )
    try:
        cfg = SignalPipelineConfig()
        ctx = signal_run_context(
            config=cfg, target_ontology_modules=[module]
        )
        detector = SignalDDetector()
        records = await detector.detect(ctx)
        assert records == []
    finally:
        with test_engine.begin() as conn:
            conn.execute(
                text("DELETE FROM extraction_claims WHERE ontology_module = :m"),
                {"m": module},
            )
