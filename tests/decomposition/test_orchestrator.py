"""Orchestrator tests (Chunk 40, CP10).

End-to-end happy path; partial-failure pause; resume Path B; dry-run;
limit; lifecycle EventTypes; OTel span hierarchy; ArcadeDB-independence.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session as SASession, sessionmaker

from src.decomposition import run_repository
from src.decomposition.config import DecompositionConfig
from src.decomposition.layer4_synthesize import synthesize_hypotheses
from src.decomposition.pipeline.orchestrator import (
    _emit_event,
    _emit_metric,
    run_decomposition,
)


pytestmark = pytest.mark.skipif(
    os.environ.get("GRACE_SKIP_DB_TESTS") == "1",
    reason="Postgres not available",
)


def _database_url() -> str:
    return os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2:///grace"
    )


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces explicit _cleanup_runs DELETE calls with connection-level
# rollback. Authorization: D485 / spec §6 Step 2.


@pytest.fixture
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = create_engine(_database_url(), future=True)
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE TABLE decomposition_runs RESTART IDENTITY CASCADE"))
    session = SASession(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()
    engine.dispose()


@pytest.fixture
def synth_archive(tmp_path: Path) -> Path:
    root = tmp_path / "archive"
    (root / "ops").mkdir(parents=True)
    (root / "finance").mkdir()
    for i in range(6):
        (root / "ops" / f"memo_{i}.txt").write_text(
            f"Ops memo {i}. Acme Inc and Beacon LLC discuss vendor terms. " * 5
        )
    for i in range(6):
        (root / "finance" / f"budget_{i}.txt").write_text(
            f"Budget item {i}. Atlas Capital reports quarterly figures. " * 5
        )
    return root


@pytest.fixture
def fake_embed():
    async def _embed(texts: list[str]) -> list[list[float]]:
        # Deterministic 8-D vectors; first half vs second half differ.
        out: list[list[float]] = []
        for i, _ in enumerate(texts):
            base = float(i % 4)
            out.append([base, base + 1, base + 2, base, 0.0, 0.5, 1.0, 1.5])
        return out

    return _embed


class _FixtureLLM:
    """LLM stub returning canned NER + Layer 4 fixtures by call order.

    Layer 3 NER calls come first (one per non-empty document), then
    Layer 4 synthesis. We classify by inspecting kwargs/system prompt
    for the synthesis sentinel string.
    """

    def __init__(self, layer4_response: str | None = None) -> None:
        self.layer4_response = layer4_response or json.dumps(
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
        self.calls: list[dict[str, Any]] = []

    async def generate(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append({"args": args, "kwargs": kwargs})
        sys_prompt = kwargs.get("system_prompt", "")
        if "segmentation analyst" in sys_prompt:
            return self.layer4_response
        # Default: emit a tiny NER payload referencing entities seen in body.
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


def _cleanup_runs(db_session, run_ids: list[UUID]) -> None:
    """No-op: SAVEPOINT-rollback in db_session handles cleanup (D485)."""
    pass


def test_orchestrator_happy_path_completes(db_session, synth_archive, fake_embed):
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
    assert result["status"] == "completed"
    assert result["layer1_summary"] is not None
    assert result["layer2_decision"] is not None
    assert result["layer3_decision"] is not None
    assert result["layer4_hypotheses"] is not None
    _cleanup_runs(db_session, [result["run_id"]])


def test_orchestrator_partial_failure_pauses_at_layer4(db_session, synth_archive, fake_embed):
    cfg = DecompositionConfig()
    # Bad Layer 4 payload — fails @model_validator (no null hypothesis).
    bad_payload = json.dumps(
        {
            "hypotheses": [
                {
                    "hypothesis_kind": "segmented",
                    "name": "X",
                    "segment_count": 1,
                    "segments": [
                        {"name": "A", "description": "a",
                         "representative_keywords": [],
                         "representative_entities": []}
                    ],
                    "agreement_summary": "agree",
                    "divergence_summary": "div",
                    "confidence_band": "medium",
                    "narrative_argument_for": "for",
                    "narrative_argument_against": "against",
                },
                {
                    "hypothesis_kind": "segmented",
                    "name": "Y",
                    "segment_count": 1,
                    "segments": [
                        {"name": "B", "description": "b",
                         "representative_keywords": [],
                         "representative_entities": []}
                    ],
                    "agreement_summary": "agree",
                    "divergence_summary": "div",
                    "confidence_band": "medium",
                    "narrative_argument_for": "for",
                    "narrative_argument_against": "against",
                },
            ],
            "synthesis_metadata": {
                "model": "qwen2.5:7b-instruct",
                "low_stability_flag": False,
                "layer3_mean_pairwise_ari": 0.5,
                "generated_at": "2026-05-07T12:00:00+00:00",
            },
        }
    )
    llm = _FixtureLLM(layer4_response=bad_payload)

    result = asyncio.run(
        run_decomposition(
            archive_root=synth_archive,
            config=cfg,
            db_session=db_session,
            embedding_provider=fake_embed,
            llm_provider=llm,
        )
    )
    assert result["status"] == "paused_pre_layer4"
    assert result["layer1_summary"] is not None
    assert result["layer2_decision"] is not None
    assert result["layer3_decision"] is not None
    assert result["layer4_hypotheses"] is None
    _cleanup_runs(db_session, [result["run_id"]])


def test_orchestrator_resume_path_b_inserts_successor(db_session, synth_archive, fake_embed):
    cfg = DecompositionConfig()
    bad_payload = json.dumps({"hypotheses": [], "synthesis_metadata": {}})
    llm = _FixtureLLM(layer4_response=bad_payload)

    paused = asyncio.run(
        run_decomposition(
            archive_root=synth_archive,
            config=cfg,
            db_session=db_session,
            embedding_provider=fake_embed,
            llm_provider=llm,
        )
    )
    assert paused["status"] == "paused_pre_layer4"

    successor = run_repository.create_resume_run(db_session, paused["run_id"])
    db_session.commit()
    assert successor["run_id"] != paused["run_id"]
    assert successor["resumed_from_run_id"] == paused["run_id"]

    _cleanup_runs(db_session, [paused["run_id"], successor["run_id"]])


def test_orchestrator_dry_run_skips_layers_2_through_4(db_session, synth_archive, fake_embed):
    cfg = DecompositionConfig()
    llm = _FixtureLLM()
    result = asyncio.run(
        run_decomposition(
            archive_root=synth_archive,
            config=cfg,
            db_session=db_session,
            embedding_provider=fake_embed,
            llm_provider=llm,
            dry_run=True,
        )
    )
    assert result["status"] == "completed"
    assert result["layer1_summary"] is not None
    assert result["layer2_decision"] is None
    assert result["layer3_decision"] is None
    assert result["layer4_hypotheses"] is None
    _cleanup_runs(db_session, [result["run_id"]])


def test_orchestrator_limit_short_circuits_inventory(db_session, synth_archive, fake_embed):
    cfg = DecompositionConfig()
    llm = _FixtureLLM()
    result = asyncio.run(
        run_decomposition(
            archive_root=synth_archive,
            config=cfg,
            db_session=db_session,
            embedding_provider=fake_embed,
            llm_provider=llm,
            dry_run=True,
            limit=3,
        )
    )
    layer1 = result["layer1_summary"]
    assert layer1["total_files"] <= 3
    assert len(layer1["files"]) <= 3
    _cleanup_runs(db_session, [result["run_id"]])


def test_orchestrator_emits_lifecycle_metric_calls(monkeypatch, db_session, synth_archive, fake_embed):
    """Verify _emit_metric is called for started + completed."""
    seen: list[str] = []

    def fake_metric(name: str, *_a, **_k) -> None:
        seen.append(name)

    monkeypatch.setattr(
        "src.decomposition.pipeline.orchestrator._emit_metric", fake_metric
    )

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
    _cleanup_runs(db_session, [result["run_id"]])


def test_orchestrator_otel_span_hierarchy(monkeypatch, db_session, synth_archive, fake_embed):
    """``decomposition.run`` is the parent of layer1–4 spans."""
    spans_started: list[str] = []

    real_tracer = __import__(
        "src.decomposition.pipeline.orchestrator", fromlist=["_tracer"]
    )._tracer

    class _RecordingTracer:
        def start_as_current_span(self, name: str):
            spans_started.append(name)
            return real_tracer.start_as_current_span(name)

    monkeypatch.setattr(
        "src.decomposition.pipeline.orchestrator._tracer",
        _RecordingTracer(),
    )

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
    assert spans_started[0] == "decomposition.run"
    assert "decomposition.layer1" in spans_started
    assert "decomposition.layer2" in spans_started
    assert "decomposition.layer3" in spans_started
    assert "decomposition.layer4" in spans_started
    _cleanup_runs(db_session, [result["run_id"]])


def test_orchestrator_does_not_import_arcadedb():
    """Decomposition is ArcadeDB-independent (no graph DB calls)."""
    import importlib
    import sys

    # Sanity: orchestrator module imports cleanly without arcade in path.
    mod = importlib.import_module("src.decomposition.pipeline.orchestrator")
    src = Path(mod.__file__).read_text(encoding="utf-8")
    assert "arcade" not in src.lower()
    # And it doesn't transitively pull arcade_client at decomposition import.
    # (Other modules may; we only assert the orchestrator file body.)
    assert "ArcadeClient" not in src
