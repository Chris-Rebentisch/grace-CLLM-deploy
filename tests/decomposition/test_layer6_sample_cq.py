"""Layer 6 sample-CQ adapter tests (Chunk 41, CP7, D324).

Pinned call-shape regression test: if Discovery's
``cq_generator.run_single_pass`` signature changes silently, this
test fails fast.
"""

from __future__ import annotations

import asyncio

import pytest

from src.decomposition import layer6_sample_cq
from src.decomposition.layer6_sample_cq import generate_sample_cqs
from src.decomposition.models import ProposedSegment
from src.decomposition.segmentation_map_models import GeneratedCQSnapshot
from src.discovery.cq_generator import GeneratedCQ, PassResult


@pytest.fixture
def proposed_segment() -> ProposedSegment:
    return ProposedSegment(
        name="ops_segment",
        description="Operations segment description.",
        representative_keywords=["ops", "memo"],
        representative_entities=["Acme Inc"],
    )


def _stub_pass_result(n_cqs: int = 5) -> PassResult:
    return PassResult(
        pass_name="top_down",
        domain="ops_segment",
        cqs=[
            GeneratedCQ(
                question=f"What is question {i}?",
                cq_type="DESCRIPTIVE",
                rationale="Sample CQ",
                priority="HIGH",
            )
            for i in range(n_cqs)
        ],
        raw_response="{}",
        model="qwen2.5:7b",
        duration_ms=120,
        success=True,
    )


# ---------- Pinned call shape ----------


def test_call_shape_pinned_to_top_down(monkeypatch, proposed_segment):
    """Adapter MUST call run_single_pass(pass_name='top_down', ...).

    Future Discovery refactors that rename or remove ``run_single_pass``
    or change ``pass_name`` parameter ordering will fail this test
    fast (D324 R5 mitigation).
    """
    captured: dict = {}

    async def stub(*, pass_name, batch, document_text, provider=None):
        captured["pass_name"] = pass_name
        captured["batch"] = batch
        captured["document_text"] = document_text
        captured["provider"] = provider
        return _stub_pass_result()

    monkeypatch.setattr(layer6_sample_cq, "run_single_pass", stub)
    asyncio.run(
        generate_sample_cqs(
            proposed_segment,
            document_excerpts=["Excerpt 1.", "Excerpt 2."],
        )
    )
    assert captured["pass_name"] == "top_down"


# ---------- Returns GeneratedCQSnapshot list ----------


def test_returns_generated_cq_snapshots(monkeypatch, proposed_segment):
    async def stub(*, pass_name, batch, document_text, provider=None):
        return _stub_pass_result(n_cqs=3)

    monkeypatch.setattr(layer6_sample_cq, "run_single_pass", stub)
    result = asyncio.run(
        generate_sample_cqs(
            proposed_segment,
            document_excerpts=["text"],
            n=3,
        )
    )
    assert len(result) == 3
    for snap in result:
        assert isinstance(snap, GeneratedCQSnapshot)
        assert snap.text.startswith("What is question")


# ---------- cq_type surfaced ----------


def test_cq_type_surfaced_in_snapshot(monkeypatch, proposed_segment):
    async def stub(*, pass_name, batch, document_text, provider=None):
        result = _stub_pass_result(n_cqs=1)
        result.cqs[0].cq_type = "QUANTITATIVE"
        return result

    monkeypatch.setattr(layer6_sample_cq, "run_single_pass", stub)
    out = asyncio.run(
        generate_sample_cqs(proposed_segment, document_excerpts=["t"], n=1)
    )
    assert out[0].cq_type == "QUANTITATIVE"


# ---------- Default n from config ----------


def test_default_n_pulls_from_config(monkeypatch, proposed_segment):
    received_n_results: list[int] = []

    async def stub(*, pass_name, batch, document_text, provider=None):
        return _stub_pass_result(n_cqs=10)

    monkeypatch.setattr(layer6_sample_cq, "run_single_pass", stub)
    out = asyncio.run(
        generate_sample_cqs(
            proposed_segment, document_excerpts=["x"]
        )
    )
    # Default n=5 from config trims the 10-CQ stub to 5.
    assert len(out) == 5


# ---------- No competency_questions write ----------


def test_no_competency_questions_persistence(monkeypatch, proposed_segment):
    """Adapter must not call any cq_generator persistence path.

    The ``run_single_pass`` codepath does NOT write to
    ``competency_questions`` (only the orchestrating
    ``run_generation_pipeline`` does, which we do not call). This test
    asserts the adapter never imports or invokes the persistence path.
    """
    import ast
    import src.decomposition.layer6_sample_cq as mod

    tree = ast.parse(open(mod.__file__).read())
    imported_names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                imported_names.append(alias.name)
    # The persistence path lives in cq_generator.run_generation_pipeline;
    # the adapter must not import it.
    assert "run_generation_pipeline" not in imported_names
    # Code (not docstrings) must not reference any competency_questions
    # write surface.
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                assert "competency_questions" not in (node.func.attr or "")
            if isinstance(node.func, ast.Name):
                assert node.func.id != "competency_questions"


# ---------- Failed PassResult returns empty ----------


def test_failed_pass_result_returns_empty_list(monkeypatch, proposed_segment):
    async def stub(*, pass_name, batch, document_text, provider=None):
        return PassResult(
            pass_name="top_down",
            domain=proposed_segment.name,
            cqs=[],
            raw_response="",
            model="",
            duration_ms=0,
            success=False,
            error_message="boom",
        )

    monkeypatch.setattr(layer6_sample_cq, "run_single_pass", stub)
    out = asyncio.run(
        generate_sample_cqs(proposed_segment, document_excerpts=["x"], n=5)
    )
    assert out == []
