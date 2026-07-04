"""Tests for two-stage schema extraction pipeline."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.discovery.cq_models import CQSource, CQStatus, CQType, CompetencyQuestion
from src.discovery.cq_database import bulk_create_cqs
from src.discovery.database import create_document
from src.discovery.models import FileType, ProcessedDocument, ProcessingStatus
from src.discovery.schema_extractor import (
    _compute_cq_coverage,
    _deduplicate_types,
    _parse_entity_types,
    _parse_relationships,
    get_schema_run,
    run_schema_extraction,
    run_stage1_pass,
    run_stage2_detail,
)
from src.discovery.schema_models import (
    PassOutput,
    ProposedEntityType,
    ProposedProperty,
    ProposedRelationship,
    SchemaExtractionRun,
    Stage1Output,
    Stage1TypeSummary,
)
from src.shared.database import get_db, get_engine
from src.shared.llm_provider import LLMResponse

# D485 carve-out (Chunk 75a): test_empty_cq_corpus requires empty-baseline.
# TRUNCATE retained with requires_db_wipe marker for D472 interlock.
pytestmark = pytest.mark.requires_db_wipe


@pytest.fixture(autouse=True)
def clean_tables():
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE competency_questions, cq_clusters, processed_documents CASCADE"))
        conn.commit()
    yield
    with engine.connect() as conn:
        conn.execute(text("TRUNCATE competency_questions, cq_clusters, processed_documents CASCADE"))
        conn.commit()


@pytest.fixture()
def db_session():
    gen = get_db()
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


def _insert_doc(db, path, domain="insurance", text_content="insurance policy coverage"):
    doc = ProcessedDocument(
        file_path=path, file_name=path.split("/")[-1], file_type=FileType.PDF,
        file_size_bytes=1024, domain=domain, word_count=len(text_content.split()),
        extracted_text=text_content, status=ProcessingStatus.COMPLETE,
    )
    return create_document(db, doc)


def _insert_cqs(db, domain="insurance", count=3, status=CQStatus.ACCEPTED):
    cqs = []
    for i in range(count):
        cq = CompetencyQuestion(
            canonical_text=f"What is the coverage of policy {i}?",
            raw_user_input=f"What is the coverage of policy {i}?",
            cq_type=CQType.SCOPING, domain=domain, source=CQSource.LLM_TOP_DOWN,
            source_pass="top_down", status=status, generation_confidence=0.8,
        )
        cqs.append(cq)
    bulk_create_cqs(db, cqs)
    return cqs


def _make_stage1_response() -> str:
    """Compact Stage 1 JSON — no properties, no evidence."""
    return json.dumps({
        "entity_types": [
            {"name": "Insurance_Policy", "parent_type": None, "description": "An insurance policy",
             "domain": "insurance", "answerable_cqs": ["abc12345"], "seed_alignment": "InsurancePolicy"},
            {"name": "Property", "description": "A real estate property", "domain": "real_estate",
             "answerable_cqs": ["ghi11111"]},
        ],
        "relationships": [
            {"name": "covers", "source_type": "Insurance_Policy", "target_type": "Property",
             "description": "Policy covers a property", "answerable_cqs": ["abc12345"]},
        ],
    })


def _make_stage2_response(type_name="Insurance_Policy") -> str:
    """Stage 2 response for one type."""
    return json.dumps({
        "name": type_name,
        "properties": [
            {"name": "policy_number", "data_type": "string", "description": "Unique ID", "required": True, "answerable_cqs": ["abc12345"]},
        ],
        "evidence_documents": ["policy.pdf"],
        "relationships_from_this_type": [
            {"name": "covers", "target_type": "Property", "description": "Covers property",
             "richness_hint": "simple", "edge_properties": [], "answerable_cqs": ["abc12345"]},
        ],
    })


def _make_mock_provider(stage1_response=None, stage2_response=None):
    """Mock provider that returns Stage 1 response first, then Stage 2 responses."""
    from src.discovery.schema_models import Stage1Output, Stage2Output

    s1 = stage1_response or _make_stage1_response()
    s2 = stage2_response or _make_stage2_response()

    async def mock_generate_structured(system_prompt, user_prompt, response_model, temperature=0.0, **kwargs):
        if response_model is Stage2Output:
            text = s2
            parsed = Stage2Output.model_validate(json.loads(s2))
        else:
            text = s1
            parsed = Stage1Output.model_validate(json.loads(s1))
        return LLMResponse(text=text, model="qwen2.5:7b", input_tokens=500, output_tokens=300,
                           duration_ms=1000, provider="ollama", parsed=parsed)

    provider = MagicMock()
    provider.generate_structured = mock_generate_structured
    provider.health_check = AsyncMock(return_value={"healthy": True, "provider": "ollama"})
    provider.provider_name = "ollama"
    provider.model = "qwen2.5:7b"
    return provider


# --- Unit tests ---


def test_compute_cq_coverage_full():
    ets = [ProposedEntityType(name="A", description="A", answerable_cqs=["cq1", "cq2"])]
    rels = [ProposedRelationship(name="r", source_type="A", target_type="B", description="r", answerable_cqs=["cq3"])]
    assert _compute_cq_coverage(ets, rels, 3) == 1.0


def test_compute_cq_coverage_partial():
    ets = [ProposedEntityType(name="A", description="A", answerable_cqs=["cq1"])]
    assert _compute_cq_coverage(ets, [], 4) == 0.25


def test_compute_cq_coverage_zero_cqs():
    assert _compute_cq_coverage([], [], 0) == 0.0


def test_parse_entity_types_valid():
    raw = [{"name": "Policy", "description": "An insurance policy",
            "properties": [{"name": "number", "data_type": "string"}], "answerable_cqs": ["cq1"]}]
    result = _parse_entity_types(raw)
    assert len(result) == 1
    assert len(result[0].properties) == 1


def test_parse_entity_types_skips_invalid():
    raw = [{"name": "Good", "description": "Valid"}, "not_a_dict", {"missing_name": True}]
    result = _parse_entity_types(raw)
    assert len(result) == 1


def test_parse_relationships_valid():
    raw = [{"name": "covers", "source_type": "Policy", "target_type": "Property", "description": "Covers",
            "richness_hint": "attributed", "edge_properties": [{"name": "coverage_amount", "data_type": "float"}]}]
    result = _parse_relationships(raw)
    assert len(result) == 1
    assert result[0].richness_hint == "attributed"


# --- Stage 1 tests ---


@pytest.mark.asyncio
async def test_stage1_pass_valid():
    """Stage 1 returns compact type summaries."""
    provider = _make_mock_provider()
    mock_cqs = []
    for i in range(3):
        class CQ:
            pass
        cq = CQ()
        cq.id = uuid4()
        cq.canonical_text = f"Question {i}"
        cq.domain = "insurance"
        mock_cqs.append(cq)

    output, dur, inp, out, model = await run_stage1_pass(
        pass_name="top_down", domain="insurance", document_text="doc content",
        cqs=mock_cqs, seed_reference_text="Seed text", config={"temperature": 0.0},
        provider=provider,
    )
    assert output is not None
    assert len(output.entity_types) == 2
    assert len(output.relationships) == 1
    assert output.entity_types[0].name == "Insurance_Policy"
    assert dur > 0


@pytest.mark.asyncio
async def test_stage1_pass_invalid_json():
    """Stage 1 with bad JSON returns None."""
    provider = _make_mock_provider(stage1_response="Not JSON!")
    output, _, _, _, _ = await run_stage1_pass(
        pass_name="bottom_up", domain="legal", document_text="text",
        cqs=[], seed_reference_text=None, config={}, provider=provider,
    )
    assert output is None


# --- Stage 2 tests ---


@pytest.mark.asyncio
async def test_stage2_detail_valid():
    """Stage 2 returns detailed ProposedEntityType."""
    provider = _make_mock_provider()
    summary = Stage1TypeSummary(
        name="Insurance_Policy", description="An insurance policy",
        domain="insurance", answerable_cqs=["abc12345"],
    )
    result = await run_stage2_detail(
        type_summary=summary, domain="insurance", document_text="doc content",
        cqs=[], seed_reference_text=None, config={}, provider=provider,
    )
    assert result is not None
    assert result.name == "Insurance_Policy"
    assert len(result.properties) == 1
    assert result.properties[0].name == "policy_number"
    assert result.evidence_documents == ["policy.pdf"]


# --- Deduplication tests ---


@pytest.mark.asyncio
async def test_deduplicate_exact_names():
    """Types with identical names are deduplicated."""
    summaries = [
        ("top_down", Stage1TypeSummary(name="Policy", description="A policy", answerable_cqs=["cq1"])),
        ("bottom_up", Stage1TypeSummary(name="Policy", description="An insurance policy", answerable_cqs=["cq1", "cq2"])),
        ("middle_out", Stage1TypeSummary(name="Unique_Type", description="Something unique")),
    ]
    # Use exact matching fallback (no embeddings)
    with patch("src.discovery.schema_extractor.embed_texts", side_effect=RuntimeError("no embeddings")):
        groups = await _deduplicate_types(summaries, threshold=0.85)
    assert len(groups) == 2  # Policy (deduped) + Unique_Type
    # The Policy group should have 2 source passes and pick the one with more CQs
    policy_group = next(g for g in groups if g[0].name == "Policy")
    assert len(policy_group[1]) == 2  # top_down + bottom_up
    assert len(policy_group[0].answerable_cqs) == 2  # best has more CQs


# --- Pipeline tests ---


@pytest.mark.asyncio
async def test_full_pipeline(db_session):
    """Full two-stage pipeline with mocked LLM."""
    _insert_doc(db_session, "/tmp/policy.pdf", "insurance", "insurance policy content")
    _insert_cqs(db_session, "insurance", count=3)

    provider = _make_mock_provider()

    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value="Seed text"),
        patch("src.discovery.schema_extractor._deduplicate_types") as mock_dedup,
    ):
        # Mock dedup to return 2 unique types
        mock_dedup.return_value = [
            (Stage1TypeSummary(name="Insurance_Policy", description="A policy", answerable_cqs=["abc12345"]), ["top_down", "bottom_up"]),
            (Stage1TypeSummary(name="Property", description="Real estate", answerable_cqs=["ghi11111"]), ["top_down"]),
        ]
        result = await run_schema_extraction(
            db=db_session, passes=["top_down", "bottom_up"], domains=["insurance"],
        )

    assert result.status == "completed"
    assert len(result.pass_outputs) == 2
    assert result.cqs_used == 3
    assert result.seed_reference_used is True


@pytest.mark.asyncio
async def test_dry_run_no_llm_calls(db_session):
    """dry_run=True builds prompts without calling LLM."""
    _insert_doc(db_session, "/tmp/doc.pdf", "insurance", "insurance content")
    _insert_cqs(db_session, "insurance", count=2)

    with patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None):
        result = await run_schema_extraction(
            db=db_session, dry_run=True, passes=["top_down"], domains=["insurance"],
        )

    assert result.status == "completed"
    assert len(result.pass_outputs) == 1
    assert result.pass_outputs[0].success is True
    assert result.total_entity_types == 0


@pytest.mark.asyncio
async def test_empty_cq_corpus(db_session):
    """Pipeline runs with no CQs."""
    _insert_doc(db_session, "/tmp/doc.pdf", "insurance", "some content")
    provider = _make_mock_provider()
    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._deduplicate_types", return_value=[]),
    ):
        result = await run_schema_extraction(db=db_session, passes=["top_down"], domains=["insurance"])
    assert result.status == "completed"
    assert result.cqs_used == 0


@pytest.mark.asyncio
async def test_no_seed_reference(db_session):
    """Pipeline runs without seed reference."""
    _insert_doc(db_session, "/tmp/doc.pdf", "insurance", "content")
    _insert_cqs(db_session, "insurance", count=2)
    provider = _make_mock_provider()
    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._deduplicate_types", return_value=[]),
    ):
        result = await run_schema_extraction(db=db_session, passes=["top_down"], domains=["insurance"])
    assert result.seed_reference_used is False


@pytest.mark.asyncio
async def test_domain_filtering(db_session):
    """--domains flag filters to specified domains only."""
    _insert_doc(db_session, "/tmp/ins.pdf", "insurance", "insurance stuff")
    _insert_doc(db_session, "/tmp/legal.pdf", "legal", "legal stuff")
    _insert_cqs(db_session, "insurance", count=2)
    provider = _make_mock_provider()
    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._deduplicate_types", return_value=[]),
    ):
        result = await run_schema_extraction(db=db_session, passes=["top_down"], domains=["insurance"])
    assert result.domains_processed == ["insurance"]


@pytest.mark.asyncio
async def test_pass_filtering(db_session):
    """--passes flag limits which passes run."""
    _insert_doc(db_session, "/tmp/doc.pdf", "insurance", "content")
    _insert_cqs(db_session, "insurance", count=2)
    provider = _make_mock_provider()
    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._deduplicate_types", return_value=[]),
    ):
        result = await run_schema_extraction(db=db_session, passes=["middle_out"], domains=["insurance"])
    assert len(result.pass_outputs) == 1
    assert result.pass_outputs[0].pass_name == "middle_out"


def test_get_schema_run():
    """In-memory storage retrieval."""
    from src.discovery.schema_extractor import _schema_runs
    run = SchemaExtractionRun(run_id="test-123")
    _schema_runs["test-123"] = run
    assert get_schema_run("test-123") is run
    assert get_schema_run("nonexistent") is None
    del _schema_runs["test-123"]


@pytest.mark.asyncio
async def test_draft_cq_fallback(db_session):
    """Falls back to DRAFT CQs when no ACCEPTED CQs exist."""
    _insert_doc(db_session, "/tmp/doc.pdf", "insurance", "content")
    _insert_cqs(db_session, "insurance", count=2, status=CQStatus.DRAFT)
    with patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None):
        result = await run_schema_extraction(
            db=db_session, dry_run=True, passes=["top_down"], domains=["insurance"],
        )
    assert result.cqs_used == 2


# --- Stage-2 deferred (skeleton) vs inline (Option B) ---


@pytest.mark.asyncio
async def test_stage2_deferred_is_default_skeleton_no_detail_calls(db_session):
    """Default 'deferred' mode emits skeleton types (empty properties), ZERO Stage-2 calls."""
    from src.discovery.schema_models import Stage2Output

    _insert_doc(db_session, "/tmp/policy.pdf", "insurance", "insurance policy content")
    _insert_cqs(db_session, "insurance", count=3)

    provider = _make_mock_provider()
    orig = provider.generate_structured
    stage2_calls = 0

    async def counting(system_prompt, user_prompt, response_model, temperature=0.0, **kwargs):
        nonlocal stage2_calls
        if response_model is Stage2Output:
            stage2_calls += 1
        return await orig(system_prompt, user_prompt, response_model, temperature, **kwargs)

    provider.generate_structured = counting

    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._deduplicate_types") as mock_dedup,
    ):
        mock_dedup.return_value = [
            (Stage1TypeSummary(name="Insurance_Policy", description="A policy", answerable_cqs=["abc12345"]), ["top_down"]),
            (Stage1TypeSummary(name="Property", description="Real estate", answerable_cqs=["ghi11111"]), ["top_down"]),
        ]
        result = await run_schema_extraction(db=db_session, passes=["top_down"], domains=["insurance"])

    assert result.status == "completed"
    assert stage2_calls == 0, "deferred mode must not make any Stage-2 detail calls"
    all_types = [t for po in result.pass_outputs for t in po.entity_types]
    assert {t.name for t in all_types} >= {"Insurance_Policy", "Property"}
    assert all(t.properties == [] for t in all_types), "skeleton types carry no properties"


@pytest.mark.asyncio
async def test_stage2_inline_details_each_type(db_session):
    """inline mode fires one Stage-2 call per unique type and fills properties."""
    from src.discovery.schema_models import Stage2Output

    _insert_doc(db_session, "/tmp/policy.pdf", "insurance", "insurance policy content")
    _insert_cqs(db_session, "insurance", count=3)

    provider = _make_mock_provider()
    orig = provider.generate_structured
    stage2_calls = 0

    async def counting(system_prompt, user_prompt, response_model, temperature=0.0, **kwargs):
        nonlocal stage2_calls
        if response_model is Stage2Output:
            stage2_calls += 1
        return await orig(system_prompt, user_prompt, response_model, temperature, **kwargs)

    provider.generate_structured = counting

    inline_cfg = {
        "passes": ["top_down"],
        "stage2_concurrency": 5,
        "stage2_dedup_threshold": 0.85,
        "stage2_mode": "inline",
        "temperature": 0.0,
    }

    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._get_schema_extraction_config", return_value=inline_cfg),
        patch("src.discovery.schema_extractor._deduplicate_types") as mock_dedup,
    ):
        mock_dedup.return_value = [
            (Stage1TypeSummary(name="Insurance_Policy", description="A policy", answerable_cqs=["abc12345"]), ["top_down"]),
            (Stage1TypeSummary(name="Property", description="Real estate", answerable_cqs=["ghi11111"]), ["top_down"]),
        ]
        result = await run_schema_extraction(db=db_session, passes=["top_down"], domains=["insurance"])

    assert result.status == "completed"
    assert stage2_calls == 2, "inline mode details each of the 2 unique types"


def test_summary_to_skeleton_type_carries_stage1_fields():
    """Skeleton conversion keeps name/hierarchy/description/CQs, empties properties."""
    from src.discovery.schema_extractor import _summary_to_skeleton_type

    s = Stage1TypeSummary(
        name="Legal_Entity", parent_type="Agent", description="An org with legal standing",
        domain="legal", answerable_cqs=["cq_001"], seed_alignment="LegalEntity",
    )
    t = _summary_to_skeleton_type(s, "legal")
    assert t.name == "Legal_Entity"
    assert t.parent_type == "Agent"
    assert t.answerable_cqs == ["cq_001"]
    assert t.seed_alignment == "LegalEntity"
    assert t.properties == []
    assert t.evidence_documents == []


# --- Batched Stage-2 detailing (ratified-subset path) ---


def _batch_provider(returned_names):
    """Mock provider whose batched Stage-2 returns details only for `returned_names`."""
    from src.discovery.schema_models import Stage2BatchOutput, Stage2BatchTypeDetail

    calls = {"n": 0, "batch_sizes": []}

    async def gen(system_prompt, user_prompt, response_model, temperature=0.0, **kwargs):
        calls["n"] += 1
        # Which requested types are in THIS batch's prompt?
        present = [n for n in returned_names if f'"{n}"' in user_prompt]
        calls["batch_sizes"].append(
            user_prompt.count('- "')  # number of "- "Name": ..." lines requested
        )
        parsed = Stage2BatchOutput(types=[
            Stage2BatchTypeDetail(
                name=n,
                properties=[{"name": "field_a", "data_type": "string",
                             "description": "d", "required": True, "answerable_cqs": []}],
                evidence_documents=[f"{n}.pdf"],
            )
            for n in present
        ])
        return LLMResponse(text="", model="m", input_tokens=1, output_tokens=1,
                           duration_ms=1, provider="ollama", parsed=parsed)

    provider = MagicMock()
    provider.generate_structured = gen
    provider.provider_name = "ollama"
    provider.model = "m"
    return provider, calls


@pytest.mark.asyncio
async def test_run_stage2_batch_matches_by_name_and_fills_properties():
    from src.discovery.schema_extractor import run_stage2_batch
    summaries = [
        Stage1TypeSummary(name="Legal_Entity", description="an org"),
        Stage1TypeSummary(name="Contract", description="a deal"),
    ]
    provider, _ = _batch_provider(["Legal_Entity", "Contract"])
    out = await run_stage2_batch(summaries, "legal", "docs", [], None, {}, provider)
    assert [t.name for t in out] == ["Legal_Entity", "Contract"]
    assert all(len(t.properties) == 1 for t in out)
    assert out[0].evidence_documents == ["Legal_Entity.pdf"]


@pytest.mark.asyncio
async def test_run_stage2_batch_degrades_missing_type_to_skeleton():
    """A type the model omits comes back as a skeleton (empty properties), not dropped."""
    from src.discovery.schema_extractor import run_stage2_batch
    summaries = [
        Stage1TypeSummary(name="Legal_Entity", description="an org"),
        Stage1TypeSummary(name="Contract", description="a deal"),
    ]
    provider, _ = _batch_provider(["Legal_Entity"])  # model omits Contract
    out = await run_stage2_batch(summaries, "legal", "docs", [], None, {}, provider)
    assert [t.name for t in out] == ["Legal_Entity", "Contract"]
    assert len(out[0].properties) == 1
    assert out[1].properties == []  # Contract degraded to skeleton


@pytest.mark.asyncio
async def test_run_stage2_batch_failure_returns_skeletons():
    from src.discovery.schema_extractor import run_stage2_batch
    summaries = [Stage1TypeSummary(name="A", description="a")]

    async def boom(*a, **k):
        raise RuntimeError("provider down")

    provider = MagicMock()
    provider.generate_structured = boom
    out = await run_stage2_batch(summaries, "legal", "docs", [], None, {}, provider)
    assert [t.name for t in out] == ["A"]
    assert out[0].properties == []


@pytest.mark.asyncio
async def test_detail_types_batches_by_size():
    """9 types at batch_size=4 -> 3 LLM calls (4+4+1); all detailed, order preserved."""
    from src.discovery.schema_extractor import detail_types
    names = [f"Type_{i}" for i in range(9)]
    summaries = [Stage1TypeSummary(name=n, description=f"d{n}") for n in names]
    provider, calls = _batch_provider(names)
    out = await detail_types(summaries, "legal", "docs", [], batch_size=4, provider=provider)
    assert [t.name for t in out] == names          # order preserved across batches
    assert calls["n"] == 3                          # ceil(9/4)
    assert calls["batch_sizes"] == [4, 4, 1]
    assert all(len(t.properties) == 1 for t in out)


@pytest.mark.asyncio
async def test_run_id_threading_reuses_polled_run(db_session):
    """run_schema_extraction(run_id=...) updates the SAME run the API/frontend polls.

    Guards the route bug where a placeholder run was created and polled, but the
    pipeline spawned a different run that did the work — leaving the UI to time out.
    """
    from src.discovery.schema_extractor import _schema_runs
    from src.discovery.schema_models import SchemaExtractionRun

    _insert_doc(db_session, "/tmp/policy.pdf", "insurance", "insurance policy content")
    _insert_cqs(db_session, "insurance", count=2)

    placeholder = SchemaExtractionRun()
    _schema_runs[placeholder.run_id] = placeholder
    pid = placeholder.run_id

    provider = _make_mock_provider()
    with (
        patch("src.discovery.schema_extractor.get_provider", return_value=provider),
        patch("src.discovery.schema_extractor._load_seed_reference_text", return_value=None),
        patch("src.discovery.schema_extractor._deduplicate_types") as mock_dedup,
    ):
        mock_dedup.return_value = [
            (Stage1TypeSummary(name="Insurance_Policy", description="A policy", answerable_cqs=["abc12345"]), ["top_down"]),
        ]
        result = await run_schema_extraction(
            db=db_session, passes=["top_down"], domains=["insurance"], run_id=pid,
        )

    # Same object the frontend would poll — now marked complete.
    assert result.run_id == pid
    assert _schema_runs[pid] is result
    assert _schema_runs[pid].status == "completed"
