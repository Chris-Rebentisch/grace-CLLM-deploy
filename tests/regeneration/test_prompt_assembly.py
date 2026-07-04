"""Tests for PromptAssembler (§5 of chunk-23-spec.md)."""

from __future__ import annotations

import pytest

from src.regeneration.prompt_assembly import (
    PromptAssembler,
    PromptAssemblyError,
)
from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import RegenerationQuery
from src.retrieval.retrieval_models import RankedResult, RetrievalResponse


def _make_retrieval_response(
    context: str, results: list[RankedResult] | None = None
) -> RetrievalResponse:
    return RetrievalResponse(
        query="q",
        results=results or [],
        serialized_context=context,
        serialization_format="template",
        total_candidates=0,
        strategy_contributions={},
        latency_ms={},
    )


def _ranked(name: str, entity_type: str, properties: dict) -> RankedResult:
    return RankedResult(
        grace_id=f"gid-{name}",
        entity_type=entity_type,
        name=name,
        properties=properties,
        rerank_score=1.0,
        rrf_score=1.0,
        contributing_strategies=["graph"],
    )


def test_sum_within_total_budget_after_assemble() -> None:
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="hello world")
    retr = _make_retrieval_response("some small context")
    out = asm.assemble(query, retr)
    assert (
        out.system_token_estimate
        + out.context_token_estimate
        + out.query_token_estimate
        == out.total_token_estimate
    )
    assert out.total_token_estimate <= settings.total_input_budget_tokens


def test_deterministic_same_inputs_same_outputs() -> None:
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="hello world", phase_state="open")
    retr = _make_retrieval_response("line one\nline two\nline three")
    a = asm.assemble(query, retr)
    b = asm.assemble(query, retr)
    assert a.model_dump_json() == b.model_dump_json()


def test_only_context_truncated_under_pressure() -> None:
    settings = RegenSettings(total_input_budget_tokens=400)
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="short query")
    long_context = "x" * 20_000  # ~5000 tokens at 4 chars/token
    retr = _make_retrieval_response(long_context)
    out = asm.assemble(query, retr)
    assert out.context_truncated is True
    assert out.system_prompt == settings.system_prompt_template.format(
        phase_style_directive=settings.phase_style_none
    )
    assert out.user_query == "short query"
    assert out.total_token_estimate <= settings.total_input_budget_tokens


def test_truncation_details_populated_when_truncated() -> None:
    settings = RegenSettings(total_input_budget_tokens=400)
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="short")
    retr = _make_retrieval_response("y" * 10_000)
    out = asm.assemble(query, retr)
    assert out.context_truncated is True
    assert out.truncation_details is not None
    assert "original_context_tokens=" in out.truncation_details
    assert "dropped_tokens=" in out.truncation_details


def test_no_truncation_when_under_budget() -> None:
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="hi")
    retr = _make_retrieval_response("small context")
    out = asm.assemble(query, retr)
    assert out.context_truncated is False
    assert out.truncation_details is None


def test_truncation_prefers_last_newline() -> None:
    settings = RegenSettings(total_input_budget_tokens=400)
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="short")
    # Build a context with a newline well within the truncation point.
    # The system prompt (incl. the D533 data-vs-instruction clause) is ~130
    # tokens; budget 400 tokens leaves ~(400 - system_est - query_est)*4 chars
    # for context. Give it enough body to force truncation and include a
    # newline before the cut.
    context = ("a" * 40) + "\n" + ("b" * 10_000)
    retr = _make_retrieval_response(context)
    out = asm.assemble(query, retr)
    assert out.context_truncated is True
    # Cut must be at the newline if it falls within budget window
    assert out.context.endswith("a" * 40) or out.context == "a" * 40 or "\n" not in out.context.strip()


def test_each_phase_state_produces_configured_directive() -> None:
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    retr = _make_retrieval_response("ctx")
    for phase, expected in [
        ("prepare", settings.phase_style_prepare),
        ("open", settings.phase_style_open),
        ("structure", settings.phase_style_structure),
        ("clarify", settings.phase_style_clarify),
        ("close", settings.phase_style_close),
        ("none", settings.phase_style_none),
    ]:
        query = RegenerationQuery(query_text="hi", phase_state=phase)
        out = asm.assemble(query, retr)
        assert out.phase_style_applied == expected
        assert expected in out.system_prompt


def test_system_plus_query_over_budget_raises() -> None:
    settings = RegenSettings(total_input_budget_tokens=10)
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="x" * 1000)
    retr = _make_retrieval_response("")
    with pytest.raises(PromptAssemblyError) as exc_info:
        asm.assemble(query, retr)
    assert "overflow=" in str(exc_info.value)


# ---------------------------------------------------------------------------
# F-0048 / ISS-0039 — compose-context supplement (property values + intent
# prose present on results must reach the composed context)
# ---------------------------------------------------------------------------


def test_supplement_adds_missing_entity_property_values() -> None:
    """CQ-21 regression: property values on results but absent from the
    inherited serialized_context are appended to the composed context."""
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="what is the policy coverage?")
    results = [
        _ranked(
            "Sablewood Umbrella Policy",
            "Insurance_Policy",
            {
                "policy_number": "GP-8894-1120",
                "coverage_limit": "5,000,000 USD",
                "jurisdiction": "BVI",
            },
        )
    ]
    # Thin inherited context: header info only, no property values.
    retr = _make_retrieval_response(
        'Entity: Insurance_Policy "Sablewood Umbrella Policy"', results=results
    )
    out = asm.assemble(query, retr)
    assert "policy_number=GP-8894-1120" in out.context
    assert "coverage_limit=5,000,000 USD" in out.context
    assert "jurisdiction=BVI" in out.context
    # Base context preserved ahead of the supplement.
    assert out.context.startswith(
        'Entity: Insurance_Policy "Sablewood Umbrella Policy"'
    )


def test_supplement_includes_intent_reasoning_prose() -> None:
    """D532/G-F5 lineage: intent-node reasoning prose hydrated into result
    properties is serialized into the composed context."""
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="why was the trust structured this way?")
    prose = "Keep the operating company bankruptcy-remote from the family trust."
    results = [
        _ranked(
            "Bankruptcy Remoteness Principle",
            "Decision_Principle",
            {"statement": prose, "applies_when": "any new entity formation"},
        )
    ]
    retr = _make_retrieval_response(
        'Entity: Decision_Principle "Bankruptcy Remoteness Principle"',
        results=results,
    )
    out = asm.assemble(query, retr)
    assert prose in out.context
    assert "applies_when=any new entity formation" in out.context


def test_supplement_skips_values_already_in_context() -> None:
    """Property pairs already serialized upstream are not repeated."""
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="q")
    results = [
        _ranked(
            "Acme",
            "Legal_Entity",
            {"jurisdiction": "BVI", "registered": "2015"},
        )
    ]
    base = 'Entity: Legal_Entity "Acme" (jurisdiction=BVI, registered=2015)'
    retr = _make_retrieval_response(base, results=results)
    out = asm.assemble(query, retr)
    assert out.context == base  # nothing missing → no supplement at all
    assert out.context.count("jurisdiction=BVI") == 1


def test_supplement_excludes_system_plane_keys() -> None:
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="q")
    results = [
        _ranked(
            "Acme",
            "Legal_Entity",
            {
                "_embedding": [0.123456] * 768,
                "sensitivity_tags": "|privileged|",
                "jurisdiction": "BVI",
            },
        )
    ]
    retr = _make_retrieval_response("some header", results=results)
    out = asm.assemble(query, retr)
    assert "jurisdiction=BVI" in out.context
    assert "_embedding" not in out.context
    assert "0.123456" not in out.context
    assert "sensitivity_tags" not in out.context


def test_supplement_truncates_oversized_property_values() -> None:
    """Oversized property sets are truncated, not crashing, budget intact."""
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="q")
    results = [
        _ranked(
            f"Entity-{i}",
            "Legal_Entity",
            {f"prop_{j}": "z" * 5_000 for j in range(30)},
        )
        for i in range(10)
    ]
    retr = _make_retrieval_response("base line", results=results)
    out = asm.assemble(query, retr)  # must not raise
    assert out.total_token_estimate <= settings.total_input_budget_tokens
    # Each rendered value is bounded — no 5000-char run survives.
    assert "z" * 5_000 not in out.context


def test_supplement_dropped_first_under_budget_pressure() -> None:
    """D134 discipline: the base context outranks the supplement — tail
    truncation removes supplement lines before touching the base context."""
    settings = RegenSettings(total_input_budget_tokens=400)
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="short")
    results = [
        _ranked("Acme", "Legal_Entity", {"jurisdiction": "BVI" * 400})
    ]
    base = "important base context line"
    retr = _make_retrieval_response(base + "\n" + "b" * 2_000, results=results)
    out = asm.assemble(query, retr)
    assert out.total_token_estimate <= settings.total_input_budget_tokens
    assert out.context.startswith(base)


def test_supplement_assembly_is_deterministic() -> None:
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="q", phase_state="open")
    results = [
        _ranked("Acme", "Legal_Entity", {"jurisdiction": "BVI", "kind": "IBC"})
    ]
    retr = _make_retrieval_response("header", results=results)
    a = asm.assemble(query, retr)
    b = asm.assemble(query, retr)
    assert a.model_dump_json() == b.model_dump_json()


def test_no_supplement_when_results_empty() -> None:
    """Pre-existing behavior preserved: results=[] leaves context verbatim."""
    settings = RegenSettings()
    asm = PromptAssembler(settings)
    query = RegenerationQuery(query_text="q")
    retr = _make_retrieval_response("verbatim context")
    out = asm.assemble(query, retr)
    assert out.context == "verbatim context"
