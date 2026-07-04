"""Tests for CQ generation pipeline."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.orm import Session

from src.discovery.cq_generator import (
    GeneratedCQ,
    GenerationRun,
    PassResult,
    _parse_cqs_from_response,
    map_cq_type_string,
    map_generated_cq_to_model,
    map_pass_to_source,
    map_priority_string,
    resolve_document_names_to_ids,
    run_generation_pipeline,
)
from src.discovery.cq_models import CQPriority, CQSource, CQStatus, CQType
from src.discovery.database import create_document
from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.shared.llm_provider import LLMResponse
from src.shared.database import get_engine


# D485 — SAVEPOINT-rollback fixture (Chunk 75a).
# Invariant: replaces TRUNCATE-based per-test isolation with SQLAlchemy 2.0
# SAVEPOINT-rollback pattern. Authorization: D485 / spec §6 Step 2.


@pytest.fixture()
def db_session():
    """Yield a SAVEPOINT-rollback session for testing (D485)."""
    engine = get_engine()
    connection = engine.connect()
    transaction = connection.begin()
    connection.execute(text("TRUNCATE competency_questions, cq_clusters, processed_documents CASCADE"))
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


def _insert_doc(db, path, domain="insurance", text_content="insurance policy coverage"):
    doc = ProcessedDocument(
        file_path=path,
        file_name=path.split("/")[-1],
        file_type=FileType.PDF,
        file_size_bytes=1024,
        domain=domain,
        word_count=len(text_content.split()),
        extracted_text=text_content,
        status=ProcessingStatus.COMPLETE,
    )
    return create_document(db, doc)


# --- Mapping tests ---


def test_map_generated_cq_top_down():
    """Verify mapping sets CQSource.LLM_TOP_DOWN."""
    gen_cq = GeneratedCQ(question="What policies exist?", cq_type="SCOPING")
    cq = map_generated_cq_to_model(gen_cq, "top_down", "insurance", [])
    assert cq.source == CQSource.LLM_TOP_DOWN
    assert cq.source_pass == "top_down"
    assert cq.status == CQStatus.DRAFT
    assert cq.generation_confidence == 0.5


def test_map_generated_cq_bottom_up():
    """Verify mapping sets CQSource.LLM_BOTTOM_UP."""
    gen_cq = GeneratedCQ(question="What is the expiry date?", cq_type="VALIDATING")
    cq = map_generated_cq_to_model(gen_cq, "bottom_up", "insurance", [])
    assert cq.source == CQSource.LLM_BOTTOM_UP


def test_map_generated_cq_middle_out():
    """Verify mapping sets CQSource.LLM_MIDDLE_OUT."""
    gen_cq = GeneratedCQ(question="Which vendors?", cq_type="RELATIONSHIP")
    cq = map_generated_cq_to_model(gen_cq, "middle_out", "operations", [])
    assert cq.source == CQSource.LLM_MIDDLE_OUT


def test_map_generated_cq_negative_evidence():
    """Verify mapping sets CQSource.LLM_GAP_FILL and coverage_gap flag."""
    gen_cq = GeneratedCQ(question="What is missing?", coverage_gap=True)
    cq = map_generated_cq_to_model(gen_cq, "negative_evidence", "insurance", [])
    assert cq.source == CQSource.LLM_GAP_FILL
    assert cq.metadata_extra.get("coverage_gap") is True


def test_map_cq_type_string_valid():
    """'SCOPING' -> CQType.SCOPING."""
    assert map_cq_type_string("SCOPING") == CQType.SCOPING
    assert map_cq_type_string("validating") == CQType.VALIDATING


def test_map_cq_type_string_invalid():
    """'UNKNOWN' -> CQType.UNCLASSIFIED."""
    assert map_cq_type_string("UNKNOWN") == CQType.UNCLASSIFIED
    assert map_cq_type_string("garbage") == CQType.UNCLASSIFIED


def test_resolve_document_names_exact(db_session):
    """Exact filename match."""
    doc = _insert_doc(db_session, "/tmp/policy.pdf")
    ids = resolve_document_names_to_ids(["policy.pdf"], db_session)
    assert len(ids) == 1
    assert ids[0] == doc.id


def test_resolve_document_names_case_insensitive(db_session):
    """Case-insensitive match."""
    doc = _insert_doc(db_session, "/tmp/Policy.PDF")
    ids = resolve_document_names_to_ids(["policy.pdf"], db_session)
    assert len(ids) == 1


def test_resolve_document_names_no_match(db_session):
    """Returns empty list for unrecognized filename."""
    _insert_doc(db_session, "/tmp/policy.pdf")
    ids = resolve_document_names_to_ids(["nonexistent.pdf"], db_session)
    assert ids == []


# --- JSON recovery tests ---


def test_json_recovery_strips_markdown():
    """LLM wraps JSON in markdown fences -> still parses."""
    raw = '```json\n[{"question": "test?", "cq_type": "SCOPING", "rationale": "", "source_document_names": [], "priority": "HIGH"}]\n```'
    from src.discovery.ollama_client import _parse_json_robust
    result = _parse_json_robust(raw)
    assert isinstance(result, list)
    cqs = _parse_cqs_from_response(result)
    assert len(cqs) == 1


def test_json_recovery_jsonl():
    """One JSON object per line -> parses as array."""
    raw = '{"question": "q1?", "cq_type": "SCOPING"}\n{"question": "q2?", "cq_type": "VALIDATING"}'
    from src.discovery.ollama_client import _parse_json_robust
    result = _parse_json_robust(raw)
    assert isinstance(result, list)
    assert len(result) == 2


# --- Pipeline tests ---


def test_pass_domain_weights_default():
    """All weights default to 1.0."""
    run = GenerationRun()
    run.pass_domain_weights = {
        "top_down": {"insurance": 1.0, "legal": 1.0},
        "bottom_up": {"insurance": 1.0, "legal": 1.0},
    }
    for pass_name, domains in run.pass_domain_weights.items():
        for domain, weight in domains.items():
            assert weight == 1.0


@pytest.mark.asyncio
async def test_generation_run_continues_on_pass_failure(db_session):
    """One pass fails, others still run."""
    _insert_doc(db_session, "/tmp/doc1.pdf", "insurance", "insurance policy content here")

    call_count = 0

    from src.discovery.cq_generator import GeneratedCQBatch, GeneratedCQItem

    async def mock_provider_generate_structured(system_prompt, user_prompt, response_model, temperature=0.0):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("Simulated failure")
        return LLMResponse(
            text="",
            model="qwen2.5:7b",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1000,
            provider="ollama",
            parsed=GeneratedCQBatch(questions=[
                GeneratedCQItem(question="test?", cq_type="SCOPING"),
            ]),
        )

    mock_provider = MagicMock()
    mock_provider.generate_structured = mock_provider_generate_structured
    mock_provider.health_check = AsyncMock(return_value={"healthy": True, "model_available": True, "provider": "ollama", "model": "qwen2.5:7b", "details": ""})
    mock_provider.provider_name = "ollama"
    mock_provider.model = "qwen2.5:7b"

    with patch("src.discovery.cq_generator.get_provider", return_value=mock_provider):
        result = await run_generation_pipeline(
            passes=["top_down", "bottom_up"],
            domains=["insurance"],
            db=db_session,
            generation_mode="multi_pass",  # this test exercises per-pass resilience
        )

    # First pass failed, second succeeded
    assert len(result.pass_results) == 2
    assert result.pass_results[0].success is False
    assert result.pass_results[1].success is True


@pytest.mark.asyncio
async def test_combined_mode_single_call_per_group(db_session):
    """Default 'combined' mode runs ONE call per document group (A3), not 4 passes."""
    _insert_doc(db_session, "/tmp/doc1.pdf", "insurance", "insurance policy content here")

    from src.discovery.cq_generator import GeneratedCQBatchReordered, GeneratedCQItemReordered

    call_count = 0

    async def mock_gen(system_prompt, user_prompt, response_model, temperature=0.0):
        nonlocal call_count
        call_count += 1
        # Combined mode must use the rationale-first schema.
        assert response_model is GeneratedCQBatchReordered
        return LLMResponse(
            text="", model="gpt-oss:120b", input_tokens=100, output_tokens=50,
            duration_ms=1000, provider="ollama",
            parsed=GeneratedCQBatchReordered(questions=[
                GeneratedCQItemReordered(rationale="defines party", cq_type="FOUNDATIONAL",
                                         question="What parties enter into an agreement?"),
            ]),
        )

    mock_provider = MagicMock()
    mock_provider.generate_structured = mock_gen
    mock_provider.health_check = AsyncMock(return_value={"healthy": True, "model_available": True, "provider": "ollama", "model": "gpt-oss:120b", "details": ""})
    mock_provider.provider_name = "ollama"
    mock_provider.model = "gpt-oss:120b"

    with patch("src.discovery.cq_generator.get_provider", return_value=mock_provider):
        result = await run_generation_pipeline(domains=["insurance"], db=db_session)

    # One document -> one group -> exactly one combined call (not 4 passes).
    assert call_count == 1
    assert len(result.pass_results) == 1
    assert result.pass_results[0].pass_name == "combined"
    assert result.total_cqs_generated == 1


@pytest.mark.asyncio
async def test_dry_run_builds_prompts_no_ollama(db_session):
    """dry_run=True builds everything without HTTP calls."""
    _insert_doc(db_session, "/tmp/doc1.pdf", "insurance", "insurance content")

    # No mocking of ollama needed — dry_run should never call it
    result = await run_generation_pipeline(
        passes=["top_down"],
        domains=["insurance"],
        dry_run=True,
        db=db_session,
    )

    assert result.total_cqs_generated == 0
    assert len(result.pass_results) == 1
    assert result.pass_results[0].success is True
    assert result.completed_at is not None
