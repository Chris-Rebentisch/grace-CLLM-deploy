"""Tests for CQ prompt templates."""

from src.discovery.cq_prompts import SYSTEM_PROMPT, build_pass_prompt


def test_build_top_down_prompt():
    """Verify template fills correctly."""
    sys_prompt, user_prompt = build_pass_prompt(
        pass_name="top_down",
        domain="insurance",
        context_digest="3 documents about insurance",
        key_terms=["policy", "coverage", "premium"],
        document_text="Sample document text here",
    )
    assert "insurance" in user_prompt
    assert "policy, coverage, premium" in user_prompt
    assert "Sample document text here" in user_prompt
    assert "STRATEGIC" in user_prompt
    assert sys_prompt == SYSTEM_PROMPT


def test_build_bottom_up_prompt():
    """Verify template fills correctly."""
    _, user_prompt = build_pass_prompt(
        pass_name="bottom_up",
        domain="legal",
        context_digest="legal context",
        key_terms=["contract"],
        document_text="Legal doc text",
    )
    assert "legal" in user_prompt
    assert "SPECIFIC FACTUAL" in user_prompt


def test_build_negative_evidence_prompt():
    """Verify template fills correctly."""
    _, user_prompt = build_pass_prompt(
        pass_name="negative_evidence",
        domain="insurance",
        context_digest="",
        key_terms=["policy", "coverage"],
        document_text="",
    )
    assert "COVERAGE GAPS" in user_prompt
    assert "insurance" in user_prompt


def test_build_middle_out_prompt():
    """Verify template fills correctly."""
    _, user_prompt = build_pass_prompt(
        pass_name="middle_out",
        domain="operations",
        context_digest="operations context",
        key_terms=["vendor", "project"],
        document_text="Operations doc text",
    )
    assert "PRACTICAL OPERATIONAL" in user_prompt
    assert "operations" in user_prompt


def test_all_prompts_contain_json_instruction():
    """All prompts say 'Output ONLY the JSON array'."""
    for pass_name in ["top_down", "bottom_up", "negative_evidence", "middle_out"]:
        _, user_prompt = build_pass_prompt(
            pass_name=pass_name,
            domain="test",
            context_digest="test",
            key_terms=["test"],
            document_text="test",
        )
        assert "Output ONLY the JSON array" in user_prompt
