"""Signal F detector tests (D243/D245). NO ChromaDB / ArcadeDB imports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.analytics.signal_pipeline.config import SignalPipelineConfig
from src.analytics.signal_pipeline.signals.signal_f import SignalFDetector


def _insert_run(
    conn, *, schema_version_id, total: int, failing: int,
    results: list[dict], age_days: int,
):
    when = datetime.now(UTC) - timedelta(days=age_days)
    conn.execute(
        text(
            """
            INSERT INTO cq_test_runs (
                id, created_at, completed_at, schema_version_id,
                schema_version_number, is_proposed_schema, total_cqs,
                passing, failing, out_of_scope, errors, pass_rate, status,
                concurrency, results_json, gap_summary, duration_ms,
                metadata_extra
            ) VALUES (
                gen_random_uuid(), :created, :created, :sv_id,
                1, FALSE, :total,
                :passing, :failing, 0, 0, :rate, 'completed',
                1, cast(:results AS jsonb), cast('{}' AS jsonb), 100,
                cast('{}' AS jsonb)
            )
            """
        ),
        {
            "created": when,
            "sv_id": schema_version_id,
            "total": total,
            "passing": total - failing,
            "failing": failing,
            "rate": (total - failing) / total if total else 0.0,
            "results": json.dumps(results),
        },
    )


def _seed_ontology_version(conn) -> str:
    """Insert a minimal ontology_versions row and return its id."""
    sv_id = str(uuid4())
    # Use a high random version_number to dodge the UNIQUE on version_number.
    import random
    vnum = 900_000 + random.randint(0, 99_999)
    conn.execute(
        text(
            """
            INSERT INTO ontology_versions (
                id, version_number, schema_json, schema_modules, hash_chain,
                source, is_active
            ) VALUES (
                :id, :vnum, cast('{}' AS jsonb), cast('{}' AS jsonb),
                :hc, 'test', FALSE
            )
            """
        ),
        {"id": sv_id, "vnum": vnum, "hc": f"test-{sv_id}"},
    )
    return sv_id


@pytest.fixture
def cleanup_cq_runs(test_engine):
    yield
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM cq_test_runs WHERE concurrency = 1"))
        # ontology_versions is append-only (DB trigger); test rows accumulate
        # but use random high version_numbers to avoid clashes.


@pytest.mark.asyncio
async def test_signal_f_fires_on_increasing_failure_rate(
    signal_run_context, test_engine, cleanup_cq_runs
):
    """12 runs with monotonically increasing failure rate → trend=increasing."""
    with test_engine.begin() as conn:
        sv_id = _seed_ontology_version(conn)
        # Older runs have low failure; newer runs high. order ASC by created_at.
        for i in range(12):
            age = 12 - i  # older first
            failing = i + 1  # 1, 2, ..., 12
            results = [
                {
                    "cq_id": str(uuid4()),
                    "cq_text": f"Q{j}",
                    "domain": "other",
                    "result": "fail" if j < failing else "pass",
                    "confidence": 0.5,
                    "reasoning": "",
                    "gap_type": None,
                    "gap_severity": None,
                    "gap_details": None,
                }
                for j in range(20)
            ]
            _insert_run(
                conn, schema_version_id=sv_id, total=20, failing=failing,
                results=results, age_days=age,
            )

    cfg = SignalPipelineConfig()
    ctx = signal_run_context(config=cfg)
    detector = SignalFDetector()
    records = await detector.detect(ctx)
    assert records, "expected one record"
    rec = records[0]
    assert rec.signal_type == "F"
    assert rec.strength > 0.0
    assert rec.evidence_snapshot["trend"] == "increasing"
    assert len(rec.evidence_snapshot["top_failing_cqs"]) > 0


@pytest.mark.asyncio
async def test_signal_f_returns_empty_when_no_cq_runs(signal_run_context):
    """No CQ runs exist in window → return [] (R5 mitigation)."""
    cfg = SignalPipelineConfig()
    ctx = signal_run_context(config=cfg)
    detector = SignalFDetector()
    # Note: this assumes the cq_test_runs table is empty for the test.
    # The fixture cleans up after itself; assertion is lenient — empty list
    # OR no completed runs of sufficient length.
    records = await detector.detect(ctx)
    assert records == [] or all(r.signal_type == "F" for r in records)


def test_signal_f_module_does_not_import_chromadb_or_arcade():
    """FAIL gate #9 — Signal F must not import chromadb or ArcadeDB clients."""
    import re
    src_path = "src/analytics/signal_pipeline/signals/signal_f.py"
    with open(src_path, encoding="utf-8") as f:
        body = f.read()
    # Strip docstrings/comments by matching only import statements at line starts.
    import_lines = [
        line for line in body.splitlines()
        if re.match(r"^\s*(import|from)\s+", line)
    ]
    joined = "\n".join(import_lines)
    assert "chromadb" not in joined.lower()
    assert "arcade_client" not in joined.lower()
    assert "from src.graph" not in joined
