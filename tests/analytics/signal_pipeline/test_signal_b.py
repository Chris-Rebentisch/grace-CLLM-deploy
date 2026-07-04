"""Signal B detector tests (D241)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.analytics.signal_pipeline.config import SignalBConfig, SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_b import SignalBDetector


def _insert_claim(
    session,
    *,
    module: str,
    chunk_id: str,
    subject: str,
    object_: str | None = None,
    is_relationship: bool = False,
    entity_type: str | None = "Thing",
    relationship_type: str | None = None,
):
    """Insert a single extraction_claim row for a Signal B fixture."""
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
                gen_random_uuid(), :unit, :entity_type, :rel_type,
                :subj, :pred, :obj, '{}'::jsonb,
                '[]'::jsonb, 'auto_accepted', 'auto', 'doc1',
                :chunk_id, :module, 1, :now,
                :fp
            )
            """
        ),
        {
            "unit": str(uuid4())[:24],
            "entity_type": None if is_relationship else entity_type,
            "rel_type": relationship_type if is_relationship else None,
            "subj": subject,
            "pred": "rel" if is_relationship else "is_a",
            "obj": object_,
            "chunk_id": chunk_id,
            "module": module,
            "now": datetime.now(UTC),
            "fp": str(uuid4()),
        },
    )


@pytest.fixture
def seed_signal_b(test_engine):
    """Seed extraction_claims with co-occurring orphans + non-orphans."""
    rows: list[dict] = []
    with test_engine.begin() as conn:
        # entities co-occurring in chunkA: Alpha + Beta + Gamma (3 distinct)
        for name in ("Alpha", "Beta", "Gamma"):
            _insert_claim(
                conn, module="signal_b_test", chunk_id="chunkA",
                subject=name, entity_type="Thing",
            )
        # one relationship Alpha-rel->Beta in another chunk
        _insert_claim(
            conn, module="signal_b_test", chunk_id="chunkA",
            subject="Alpha", object_="Beta", is_relationship=True,
            relationship_type="rel", entity_type=None,
        )
    yield "signal_b_test"
    with test_engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM extraction_claims WHERE ontology_module = :m"
            ),
            {"m": "signal_b_test"},
        )


@pytest.mark.asyncio
async def test_signal_b_detects_orphan_pairs(signal_run_context, seed_signal_b):
    cfg = SignalPipelineConfig(signal_b=SignalBConfig(current_window_days=7))
    ctx = signal_run_context(
        config=cfg,
        target_ontology_modules=[seed_signal_b],
    )
    detector = SignalBDetector()
    records = await detector.detect(ctx)
    assert records, "expected at least one record"
    rec = next(r for r in records if r.ontology_module == seed_signal_b)
    # 3 entities → 3 unordered pairs; 1 has a relationship → 2 orphans.
    ev = rec.evidence_snapshot
    assert ev["total_pairs"] >= 3
    assert ev["orphan_pairs"] >= 2
    assert rec.strength > 0.0


@pytest.mark.asyncio
async def test_signal_b_zero_when_no_orphan_pairs(signal_run_context, test_engine):
    """All co-occurring pairs have a relationship → 0 strength."""
    module = "signal_b_dense"
    with test_engine.begin() as conn:
        for name in ("X", "Y"):
            _insert_claim(
                conn, module=module, chunk_id="ch1",
                subject=name, entity_type="Thing",
            )
        _insert_claim(
            conn, module=module, chunk_id="ch1",
            subject="X", object_="Y", is_relationship=True,
            relationship_type="rel", entity_type=None,
        )
    try:
        cfg = SignalPipelineConfig(signal_b=SignalBConfig(current_window_days=7))
        ctx = signal_run_context(
            config=cfg, target_ontology_modules=[module]
        )
        detector = SignalBDetector()
        records = await detector.detect(ctx)
        rec = next(r for r in records if r.ontology_module == module)
        assert rec.strength == 0.0
        assert rec.evidence_snapshot["orphan_pairs"] == 0
    finally:
        with test_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM extraction_claims WHERE ontology_module = :m"
                ),
                {"m": module},
            )
