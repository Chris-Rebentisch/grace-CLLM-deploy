"""Tests for two-stage schema extraction prompt construction."""

from uuid import uuid4

from src.discovery.schema_prompts import (
    STAGE1_RESPONSE_SCHEMA,
    STAGE1_SYSTEM_PROMPT,
    STAGE2_SYSTEM_PROMPT,
    build_stage1_prompt,
    build_stage2_prompt,
)


def _make_cqs(n=3):
    """Create mock CQ-like objects for testing."""
    cqs = []
    for i in range(n):
        class MockCQ:
            pass
        cq = MockCQ()
        cq.id = uuid4()
        cq.canonical_text = f"What is the coverage of policy {i}?"
        cq.domain = "insurance"
        cqs.append(cq)
    return cqs


SEED_TEXT = """=== Seed Ontology Reference ===
Industry: real_estate
--- FIBO ---
  LegalEntity (subclass of Agent)
"""

SAMPLE_DOC_TEXT = "--- Document: policy.pdf ---\nInsurance policy for property at 123 Main St."


# --- Stage 1 tests ---


def test_stage1_top_down_includes_seed():
    """Top-down Stage 1 includes seed as structural patterns."""
    cqs = _make_cqs()
    system, user = build_stage1_prompt(
        pass_name="top_down", domain="insurance",
        document_text=SAMPLE_DOC_TEXT, cqs=cqs, seed_reference_text=SEED_TEXT,
    )
    assert "Top-Down" in user
    assert "structural patterns" in user
    assert "FIBO" in user
    assert SAMPLE_DOC_TEXT in user


def test_stage1_bottom_up_ignores_seed():
    """Bottom-up Stage 1 tells LLM to ignore seed initially."""
    cqs = _make_cqs()
    _, user = build_stage1_prompt(
        pass_name="bottom_up", domain="insurance",
        document_text=SAMPLE_DOC_TEXT, cqs=cqs, seed_reference_text=SEED_TEXT,
    )
    assert "Bottom-Up" in user
    assert "IGNORE the seed reference" in user


def test_stage1_middle_out_vocabulary():
    """Middle-out Stage 1 uses seed for vocabulary."""
    cqs = _make_cqs()
    _, user = build_stage1_prompt(
        pass_name="middle_out", domain="insurance",
        document_text=SAMPLE_DOC_TEXT, cqs=cqs, seed_reference_text=SEED_TEXT,
    )
    assert "Middle-Out" in user
    assert "naming patterns" in user


def test_stage1_cq_ids_in_prompt():
    """CQ IDs (first 8 chars) included in Stage 1 prompt."""
    cqs = _make_cqs(2)
    _, user = build_stage1_prompt(
        pass_name="top_down", domain="insurance",
        document_text=SAMPLE_DOC_TEXT, cqs=cqs, seed_reference_text=SEED_TEXT,
    )
    for cq in cqs:
        short_id = str(cq.id)[:8]
        assert short_id in user


def test_stage1_no_properties_instruction():
    """Stage 1 system prompt says no properties."""
    assert "Do NOT include properties" in STAGE1_SYSTEM_PROMPT


def test_stage1_no_seed():
    """Stage 1 works without seed reference."""
    cqs = _make_cqs()
    _, user = build_stage1_prompt(
        pass_name="top_down", domain="insurance",
        document_text=SAMPLE_DOC_TEXT, cqs=cqs, seed_reference_text=None,
    )
    assert "FIBO" not in user
    assert SAMPLE_DOC_TEXT in user


def test_stage1_response_schema_compact():
    """Stage 1 response schema omits properties but carries reviewer-facing fields.

    Properties stay deferred to Stage 2 (skeleton-first, proposed D525), but Stage 1
    now also emits plain-English presentation fields + evidence documents so the
    business-reader review screen has real grounding (display_label, plain_description,
    example_snippet, evidence_documents).
    """
    assert "properties" not in STAGE1_RESPONSE_SCHEMA
    assert "entity_types" in STAGE1_RESPONSE_SCHEMA
    # Reviewer-facing grounding fields are intentionally present in Stage 1.
    assert "display_label" in STAGE1_RESPONSE_SCHEMA
    assert "plain_description" in STAGE1_RESPONSE_SCHEMA
    assert "example_snippet" in STAGE1_RESPONSE_SCHEMA
    assert "evidence_documents" in STAGE1_RESPONSE_SCHEMA


def test_stage1_other_domain_guidance():
    """'other' domain gets extra guidance in Stage 1."""
    cqs = _make_cqs()
    _, user = build_stage1_prompt(
        pass_name="top_down", domain="other",
        document_text=SAMPLE_DOC_TEXT, cqs=cqs,
    )
    assert "multiple business areas" in user


def test_stage1_invalid_pass_raises():
    """Unknown pass name raises ValueError."""
    import pytest
    with pytest.raises(ValueError, match="Unknown pass"):
        build_stage1_prompt(
            pass_name="invalid", domain="insurance",
            document_text="text", cqs=[],
        )


# --- Stage 2 tests ---


def test_stage2_prompt_includes_type_name():
    """Stage 2 prompt mentions the specific type name."""
    cqs = _make_cqs()
    _, user = build_stage2_prompt(
        type_name="Insurance_Policy",
        type_description="An insurance policy document",
        domain="insurance",
        document_text=SAMPLE_DOC_TEXT,
        cqs=cqs,
    )
    assert "Insurance_Policy" in user
    assert "insurance policy document" in user


def test_stage2_asks_for_properties():
    """Stage 2 system prompt asks for properties and evidence."""
    assert "properties" in STAGE2_SYSTEM_PROMPT.lower()
    assert "evidence" in STAGE2_SYSTEM_PROMPT.lower()


def test_stage2_includes_seed():
    """Stage 2 includes seed reference when available."""
    cqs = _make_cqs()
    _, user = build_stage2_prompt(
        type_name="Legal_Entity",
        type_description="An entity with legal standing",
        domain="corporate_structure",
        document_text=SAMPLE_DOC_TEXT,
        cqs=cqs,
        seed_reference_text=SEED_TEXT,
    )
    assert "FIBO" in user
    assert "Seed reference" in user


def test_stage2_no_seed():
    """Stage 2 works without seed reference."""
    cqs = _make_cqs()
    _, user = build_stage2_prompt(
        type_name="Policy",
        type_description="A policy",
        domain="insurance",
        document_text=SAMPLE_DOC_TEXT,
        cqs=cqs,
        seed_reference_text=None,
    )
    assert "Policy" in user
    assert "FIBO" not in user


def test_stage1_system_prompt_consistent():
    """System prompt is the same across all Stage 1 passes."""
    cqs = _make_cqs()
    prompts = []
    for pass_name in ["top_down", "bottom_up", "middle_out"]:
        system, _ = build_stage1_prompt(
            pass_name=pass_name, domain="insurance",
            document_text=SAMPLE_DOC_TEXT, cqs=cqs,
        )
        prompts.append(system)
    assert prompts[0] == prompts[1] == prompts[2]
    assert "ontology engineer" in prompts[0]


def test_cq_corpus_handles_dicts():
    """CQ corpus formatting works with dict inputs."""
    cqs = [
        {"id": str(uuid4()), "canonical_text": "What are the total assets?"},
    ]
    _, user = build_stage1_prompt(
        pass_name="top_down", domain="corporate_structure",
        document_text="doc text", cqs=cqs,
    )
    assert "total assets" in user


# --- Prompt-cache ordering guards (Ollama/llama.cpp prefix reuse) ---
#
# The large, call-invariant block (DOCUMENTS + CQ corpus) must sit at the FRONT
# of the prompt — before any pass/type-specific text — so its KV cache is reused
# across calls instead of the ~48k-token corpus being re-prefilled every time.


def test_stage1_corpus_is_cacheable_prefix_all_passes():
    """For every Stage-1 pass, document_text + CQ corpus precede the variable tail."""
    cqs = _make_cqs()
    marker = "ZZ_UNIQUE_DOC_MARKER"
    doc = f"--- Document: x.pdf ---\n{marker} body text"
    for pass_name in ("top_down", "bottom_up", "middle_out"):
        _system, user = build_stage1_prompt(
            pass_name=pass_name, domain="insurance",
            document_text=doc, cqs=cqs, seed_reference_text=SEED_TEXT,
        )
        doc_idx = user.index(marker)
        approach_idx = user.index("APPROACH:")
        cq_idx = user.index("coverage of policy 0")
        # Corpus and CQs come before the pass-specific APPROACH divergence.
        assert doc_idx < approach_idx, f"{pass_name}: doc must precede APPROACH"
        assert cq_idx < approach_idx, f"{pass_name}: CQs must precede APPROACH"


def test_stage1_shared_prefix_identical_across_passes():
    """The cacheable head (up to 'APPROACH:') is byte-identical across passes."""
    cqs = _make_cqs()
    heads = []
    for pass_name in ("top_down", "bottom_up", "middle_out"):
        _system, user = build_stage1_prompt(
            pass_name=pass_name, domain="insurance",
            document_text=SAMPLE_DOC_TEXT, cqs=cqs, seed_reference_text=SEED_TEXT,
        )
        heads.append(user[: user.index("APPROACH:")])
    assert heads[0] == heads[1] == heads[2], "shared prefix must match for KV reuse"


def test_stage2_corpus_is_cacheable_prefix():
    """Stage-2: document_text + CQ corpus precede the per-type instruction."""
    cqs = _make_cqs()
    marker = "ZZ_UNIQUE_DOC_MARKER"
    doc = f"--- Document: x.pdf ---\n{marker} body text"
    _system, user = build_stage2_prompt(
        type_name="Insurance_Policy", type_description="a policy",
        domain="insurance", document_text=doc, cqs=cqs, seed_reference_text=SEED_TEXT,
    )
    assert user.index(marker) < user.index('Detail the entity type')
