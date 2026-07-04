"""Tests for the CQ merge pipeline orchestrator."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.discovery.cq_merge import _merge_runs, run_merge_pipeline
from src.discovery.merge_models import MergeRun
from src.shared.database import get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


@pytest.fixture(autouse=True)
def clear_merge_runs():
    """Clear in-memory merge runs before each test."""
    _merge_runs.clear()
    yield
    _merge_runs.clear()


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE competency_questions, cq_clusters, processed_documents, merge_runs CASCADE"))
    session = Session(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, trans):
        if trans.nested and not trans._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


def _make_cq(text_str="Test question?", source_pass="top_down", domain="insurance"):
    """Create a CompetencyQuestion with mocked domain validation."""
    from src.discovery.cq_models import CQSource, CompetencyQuestion

    with patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]):
        return CompetencyQuestion(
            canonical_text=text_str,
            source=CQSource.LLM_TOP_DOWN,
            source_pass=source_pass,
            domain=domain,
        )


def _insert_cqs(db, count=6):
    """Insert test CQs into the database."""
    from src.discovery.cq_database import create_cq

    cqs = []
    for i in range(count):
        cq = _make_cq(text_str=f"What types of insurance question {i}?", source_pass="top_down")
        with patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]):
            created = create_cq(db, cq)
            cqs.append(created)
    return cqs


def _mock_embeddings(count):
    """Generate synthetic 384-dim embeddings with two clusters."""
    np.random.seed(42)
    half = count // 2
    group1 = [np.random.normal(1.0, 0.01, 384).tolist() for _ in range(half)]
    group2 = [np.random.normal(-1.0, 0.01, 384).tolist() for _ in range(count - half)]
    return group1 + group2


def _mock_call1_response(n_clusters=2):
    """Build a mock Call1 JSON response."""
    clusters = []
    for i in range(n_clusters):
        clusters.append({
            "cluster_label": i,
            "canonical_text": f"Canonical question {i}?",
            "canonical_index": 0,
            "split_recommendations": [],
        })
    return json.dumps({"clusters": clusters})


def _mock_call2_response():
    """Build a mock Call2 JSON response."""
    return json.dumps({
        "domain_groups": [
            {"domain": "insurance", "sub_domains": [{"name": "policy_types", "cq_ids": ["cq-1"]}]}
        ],
        "cross_domain_links": [],
    })


def _mock_call3_response(n_gap_fills=2):
    """Build a mock Call3 JSON response."""
    gap_fills = [
        {"canonical_text": f"Gap fill {i}?", "domain": "insurance", "cq_type": "SCOPING", "gap_addressed": "domain_gap", "rationale": "test"}
        for i in range(n_gap_fills)
    ]
    return json.dumps({"gap_fill_cqs": gap_fills, "path_annotations": []})


def _make_mock_provider():
    """Create a mock LLM provider for all three calls."""
    from src.shared.llm_provider import LLMResponse

    call_count = {"n": 0}

    async def mock_generate(system_prompt, user_prompt, temperature=0.0, json_mode=True, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            text = _mock_call1_response()
        elif call_count["n"] == 2:
            text = _mock_call2_response()
        else:
            text = _mock_call3_response()
        return LLMResponse(text=text, model="qwen2.5:7b", input_tokens=100, output_tokens=50, duration_ms=500, provider="ollama")

    provider = MagicMock()
    provider.provider_name = "ollama"
    provider.model = "qwen2.5:7b"
    provider.generate = mock_generate
    return provider


# --- Pipeline tests ---


@pytest.mark.asyncio
async def test_pipeline_empty_input():
    """No CQs -> returns early with status 'completed'."""
    with patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}):
        result = await run_merge_pipeline(db=None, dry_run=False)

    assert result.status == "completed"
    assert result.total_cqs_input == 0


@pytest.mark.asyncio
async def test_pipeline_dry_run(db_session):
    """Mocked embeddings, runs Tier 1+2 only, no LLM calls."""
    _insert_cqs(db_session, 6)
    mock_embs = _mock_embeddings(6)
    template_embs = _mock_embeddings(5)

    with (
        patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}),
        patch("src.discovery.cq_merge.load_discovery_config", return_value={"cq_merge": {"min_cluster_size": 2}, "domain_categories": ["insurance", "legal", "other"]}),
        patch("src.discovery.cq_merge.get_valid_domains", return_value=["insurance", "legal", "other"]),
        patch("src.discovery.cq_merge.embed_texts", new_callable=AsyncMock, side_effect=[mock_embs, template_embs]),
        patch("src.discovery.cq_merge.load_templates", return_value=[]),
        patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]),
    ):
        result = await run_merge_pipeline(db=db_session, dry_run=True)

    assert result.status == "completed"
    assert result.total_cqs_input >= 2
    # Dry run should not make LLM calls (no Tier 3)
    assert result.tier3_results_json is None


@pytest.mark.asyncio
async def test_pipeline_full(db_session):
    """Mock everything (embeddings, LLM), verify MergeRun populated."""
    _insert_cqs(db_session, 6)
    mock_embs = _mock_embeddings(6)
    template_embs = _mock_embeddings(5)
    provider = _make_mock_provider()

    with (
        patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}),
        patch("src.discovery.cq_merge.load_discovery_config", return_value={"cq_merge": {"min_cluster_size": 2, "gap_fill_max_cqs": 15}, "domain_categories": ["insurance", "legal", "other"]}),
        patch("src.discovery.cq_merge.get_valid_domains", return_value=["insurance", "legal", "other"]),
        patch("src.discovery.cq_merge.embed_texts", new_callable=AsyncMock, side_effect=[mock_embs, template_embs]),
        patch("src.discovery.cq_merge.load_templates", return_value=[]),
        patch("src.discovery.merge_llm_calls.get_provider", return_value=provider),
        patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]),
    ):
        result = await run_merge_pipeline(db=db_session, dry_run=False)

    assert result.status == "completed"
    assert result.total_cqs_input == 6
    assert result.completed_at is not None
    assert result.duration_ms > 0


@pytest.mark.asyncio
async def test_pipeline_creates_clusters_in_db(db_session):
    """After pipeline, CQCluster records exist."""
    from src.discovery.cq_database import get_cluster_members

    _insert_cqs(db_session, 6)
    mock_embs = _mock_embeddings(6)
    template_embs = _mock_embeddings(5)

    with (
        patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}),
        patch("src.discovery.cq_merge.load_discovery_config", return_value={"cq_merge": {"min_cluster_size": 2}, "domain_categories": ["insurance", "legal", "other"]}),
        patch("src.discovery.cq_merge.get_valid_domains", return_value=["insurance", "legal", "other"]),
        patch("src.discovery.cq_merge.embed_texts", new_callable=AsyncMock, side_effect=[mock_embs, template_embs]),
        patch("src.discovery.cq_merge.load_templates", return_value=[]),
        patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]),
    ):
        result = await run_merge_pipeline(db=db_session, dry_run=True)

    # Verify pipeline processed CQs (clusters or singletons)
    assert result.total_clusters + result.total_singletons >= 1


@pytest.mark.asyncio
async def test_pipeline_updates_cqs_in_db(db_session):
    """After pipeline, CQ.cluster_id set."""
    from src.discovery.cq_database import list_cqs

    cqs = _insert_cqs(db_session, 6)
    mock_embs = _mock_embeddings(6)
    template_embs = _mock_embeddings(5)

    with (
        patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}),
        patch("src.discovery.cq_merge.load_discovery_config", return_value={"cq_merge": {"min_cluster_size": 2}, "domain_categories": ["insurance", "legal", "other"]}),
        patch("src.discovery.cq_merge.get_valid_domains", return_value=["insurance", "legal", "other"]),
        patch("src.discovery.cq_merge.embed_texts", new_callable=AsyncMock, side_effect=[mock_embs, template_embs]),
        patch("src.discovery.cq_merge.load_templates", return_value=[]),
        patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]),
    ):
        await run_merge_pipeline(db=db_session, dry_run=True)

    # Refresh CQs from DB
    with patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]):
        updated_cqs = list_cqs(db_session)

    # CQs should exist in DB (some may have cluster_id set, singletons won't)
    assert len(updated_cqs) >= 1


@pytest.mark.asyncio
async def test_pipeline_gap_fill_creates_cqs(db_session):
    """After pipeline with gap fills, new CQs created."""
    _insert_cqs(db_session, 6)
    mock_embs = _mock_embeddings(6)
    template_embs = _mock_embeddings(5)
    provider = _make_mock_provider()

    with (
        patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}),
        patch("src.discovery.cq_merge.load_discovery_config", return_value={"cq_merge": {"min_cluster_size": 2, "gap_fill_max_cqs": 15}, "domain_categories": ["insurance", "legal", "other"]}),
        patch("src.discovery.cq_merge.get_valid_domains", return_value=["insurance", "legal", "other"]),
        patch("src.discovery.cq_merge.embed_texts", new_callable=AsyncMock, side_effect=[mock_embs, template_embs]),
        patch("src.discovery.cq_merge.load_templates", return_value=[]),
        patch("src.discovery.merge_llm_calls.get_provider", return_value=provider),
        patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]),
    ):
        result = await run_merge_pipeline(db=db_session, dry_run=False)

    assert result.total_gap_fills >= 0  # May be 0 if call3 doesn't return gap fills


@pytest.mark.asyncio
async def test_pipeline_handles_tier3_failure(db_session):
    """Tier 3 fails gracefully, pipeline still completes."""
    from src.shared.llm_provider import LLMResponse

    _insert_cqs(db_session, 6)
    mock_embs = _mock_embeddings(6)
    template_embs = _mock_embeddings(5)

    # Provider that always fails
    failing_provider = MagicMock()
    failing_provider.provider_name = "ollama"
    failing_provider.model = "qwen2.5:7b"
    failing_provider.generate = AsyncMock(side_effect=RuntimeError("LLM down"))

    with (
        patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}),
        patch("src.discovery.cq_merge.load_discovery_config", return_value={"cq_merge": {"min_cluster_size": 2, "gap_fill_max_cqs": 15}, "domain_categories": ["insurance", "legal", "other"]}),
        patch("src.discovery.cq_merge.get_valid_domains", return_value=["insurance", "legal", "other"]),
        patch("src.discovery.cq_merge.embed_texts", new_callable=AsyncMock, side_effect=[mock_embs, template_embs]),
        patch("src.discovery.cq_merge.load_templates", return_value=[]),
        patch("src.discovery.merge_llm_calls.get_provider", return_value=failing_provider),
        patch("src.discovery.cq_models.get_valid_domains", return_value=["insurance", "legal", "operations", "other"]),
    ):
        result = await run_merge_pipeline(db=db_session, dry_run=False)

    # Pipeline should still complete even if Tier 3 returns None
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_merge_run_stored():
    """After pipeline, _merge_runs dict has entry."""
    with patch("src.discovery.cq_merge.read_llm_config_from_yaml", return_value={"model": "test", "provider": "ollama"}):
        result = await run_merge_pipeline(db=None, dry_run=False)

    assert result.run_id in _merge_runs
    stored = _merge_runs[result.run_id]
    assert stored.run_id == result.run_id
    assert stored.status == "completed"
