"""Telemetry surface tests for the decomposition pipeline (Chunk 40, CP11).

Asserts:
* Three new D318 OTel counters are importable from
  ``src.analytics.metrics``.
* The metric-contract GOLDEN_NAMES set includes the six expected
  Chunk 40 entries (stripped + ``_total`` form per Chunk 37–39
  convention).
* Three new ``decomposition_run_*`` event types resolve to their
  payload models in ``src.elicitation.models._PAYLOAD_MODELS``.
* The orchestrator emits both lifecycle counters on a happy-path run.
* The orchestrator opens a parent ``decomposition.run`` span with
  layer1–layer4 children.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from src.analytics import metrics as metrics_module
from src.elicitation.models import (
    DecompositionRunCompletedPayload,
    DecompositionRunFailedPayload,
    DecompositionRunStartedPayload,
    _PAYLOAD_MODELS,
)
from tests.analytics.test_metric_contract import GOLDEN_NAMES


def test_three_new_otel_counters_are_registered():
    """``src.analytics.metrics`` exposes the three new D318 counters."""
    assert hasattr(metrics_module, "grace_decomposition_runs_started_total")
    assert hasattr(metrics_module, "grace_decomposition_runs_completed_total")
    assert hasattr(metrics_module, "grace_decomposition_runs_failed_total")


def test_golden_names_includes_chunk_40_entries():
    """GOLDEN_NAMES allowlists both stripped and ``_total`` forms."""
    expected = {
        "grace_decomposition_runs_started",
        "grace_decomposition_runs_started_total",
        "grace_decomposition_runs_completed",
        "grace_decomposition_runs_completed_total",
        "grace_decomposition_runs_failed",
        "grace_decomposition_runs_failed_total",
    }
    assert expected.issubset(GOLDEN_NAMES), (
        "Chunk 40 GOLDEN_NAMES entries missing: "
        f"{sorted(expected - GOLDEN_NAMES)}"
    )


def test_payload_models_resolve_in_registry():
    """Three new event types map to their payload classes."""
    assert _PAYLOAD_MODELS["decomposition_run_started"] is DecompositionRunStartedPayload
    assert _PAYLOAD_MODELS["decomposition_run_completed"] is DecompositionRunCompletedPayload
    assert _PAYLOAD_MODELS["decomposition_run_failed"] is DecompositionRunFailedPayload


pytestmark_db = pytest.mark.skipif(
    os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
    reason="Postgres not available",
)


@pytest.fixture
def db_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    url = os.environ.get("DATABASE_URL", "postgresql+psycopg2:///grace")
    engine = create_engine(url, future=True)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()
        engine.dispose()


@pytest.fixture
def synth_archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    (root / "ops").mkdir(parents=True)
    for i in range(4):
        (root / "ops" / f"memo_{i}.txt").write_text(
            f"Ops memo {i}. Acme Inc and Beacon LLC discuss vendor terms. " * 5
        )
    return root


@pytest.fixture
def fake_embed():
    async def _embed(texts):
        out = []
        for i, _ in enumerate(texts):
            base = float(i % 4)
            out.append([base, base + 1, base + 2, base, 0.0, 0.5, 1.0, 1.5])
        return out

    return _embed


class _FixtureLLM:
    def __init__(self) -> None:
        self.layer4_response = json.dumps(
            {
                "hypotheses": [
                    {
                        "hypothesis_kind": "segmented",
                        "name": "Two-segment org",
                        "segment_count": 2,
                        "segments": [
                            {"name": "Operations", "description": "ops",
                             "representative_keywords": ["ops"],
                             "representative_entities": ["Acme Inc"]},
                            {"name": "Finance", "description": "fin",
                             "representative_keywords": ["budget"],
                             "representative_entities": ["Atlas Capital"]},
                        ],
                        "agreement_summary": "agree",
                        "divergence_summary": "diverge",
                        "confidence_band": "medium",
                        "narrative_argument_for": "for",
                        "narrative_argument_against": "against",
                    },
                    {
                        "hypothesis_kind": "null",
                        "name": "Null hypothesis: undifferentiated whole",
                        "narrative_argument_for": "for",
                        "narrative_argument_against": "against",
                        "confidence_band": "low",
                    },
                ],
                "synthesis_metadata": {
                    "model": "qwen2.5:7b-instruct",
                    "low_stability_flag": False,
                    "layer3_mean_pairwise_ari": 0.85,
                    "generated_at": "2026-05-07T12:00:00+00:00",
                },
            }
        )

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        sys_prompt = kwargs.get("system_prompt", "")
        if "segmentation analyst" in sys_prompt:
            return self.layer4_response
        return json.dumps(
            {
                "mentions": [
                    {"text": "Acme Inc", "category": "organization",
                     "occurrence_count": 1},
                    {"text": "Atlas Capital", "category": "organization",
                     "occurrence_count": 1},
                ]
            }
        )


def _cleanup(session, ids):
    from sqlalchemy import text
    try:
        session.execute(text("SET LOCAL alembic.downgrading = 'true'"))
        session.execute(
            text("DELETE FROM decomposition_runs WHERE run_id = ANY(:ids)"),
            {"ids": ids},
        )
        session.commit()
    except Exception:
        session.rollback()


@pytestmark_db
def test_orchestrator_emits_started_and_completed_counters(
    monkeypatch, db_session, synth_archive, fake_embed
):
    seen: list[str] = []

    def fake_metric(name: str, *_a, **_k) -> None:
        seen.append(name)

    monkeypatch.setattr(
        "src.decomposition.pipeline.orchestrator._emit_metric", fake_metric
    )

    from src.decomposition.config import DecompositionConfig
    from src.decomposition.pipeline.orchestrator import run_decomposition

    cfg = DecompositionConfig()
    llm = _FixtureLLM()
    result = asyncio.run(
        run_decomposition(
            archive_root=synth_archive,
            config=cfg,
            db_session=db_session,
            embedding_provider=fake_embed,
            llm_provider=llm,
        )
    )

    assert "grace_decomposition_runs_started_total" in seen
    assert "grace_decomposition_runs_completed_total" in seen
    _cleanup(db_session, [result["run_id"]])


@pytestmark_db
def test_orchestrator_opens_parent_run_span_and_layer_children(
    monkeypatch, db_session, synth_archive, fake_embed
):
    spans: list[str] = []

    real_tracer = __import__(
        "src.decomposition.pipeline.orchestrator", fromlist=["_tracer"]
    )._tracer

    class _RecordingTracer:
        def start_as_current_span(self, name: str):
            spans.append(name)
            return real_tracer.start_as_current_span(name)

    monkeypatch.setattr(
        "src.decomposition.pipeline.orchestrator._tracer", _RecordingTracer()
    )

    from src.decomposition.config import DecompositionConfig
    from src.decomposition.pipeline.orchestrator import run_decomposition

    cfg = DecompositionConfig()
    llm = _FixtureLLM()
    result = asyncio.run(
        run_decomposition(
            archive_root=synth_archive,
            config=cfg,
            db_session=db_session,
            embedding_provider=fake_embed,
            llm_provider=llm,
        )
    )

    assert spans[0] == "decomposition.run"
    for child in (
        "decomposition.layer1",
        "decomposition.layer2",
        "decomposition.layer3",
        "decomposition.layer4",
    ):
        assert child in spans, f"Missing child span: {child}"
    _cleanup(db_session, [result["run_id"]])
