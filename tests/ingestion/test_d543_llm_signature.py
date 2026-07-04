"""Guard tests for D543 — ingestion LLM provider-interface drift.

The LLMProvider interface is ``generate(system_prompt, user_prompt, ...) -> LLMResponse``,
but the ingestion bounded-heat stages (T4 triage, voice_tone synthesis/feature/
signature/recipient/redactor, corroboration LLM fallback) were written against the old
``generate(prompt) -> str`` signature — so the call raised "missing user_prompt" and the
model was never actually invoked (callers fell through to defaults). These tests bind the
corrected call shape so the regression can't silently return.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

REPO = Path(__file__).resolve().parents[2]


def _generate_calls(tree: ast.AST):
    """Yield ast.Call nodes that invoke ``<x>.generate(...)`` (not generate_structured/vision)."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "generate"):
            yield node


def test_d543_no_ingestion_generate_uses_stale_signature():
    """No ``.generate()`` call in src/ingestion may pass a bare ``prompt`` (positional
    single-arg or ``prompt=`` keyword) — that is the stale signature. Every call must
    supply ``user_prompt`` (and, by convention, ``system_prompt``)."""
    offenders: list[str] = []
    for py in (REPO / "src" / "ingestion").rglob("*.py"):
        tree = ast.parse(py.read_text())
        for call in _generate_calls(tree):
            kw = {k.arg for k in call.keywords if k.arg}
            if "prompt" in kw:
                offenders.append(f"{py.relative_to(REPO)}:{call.lineno} uses prompt= kwarg")
            elif "user_prompt" not in kw and len(call.args) < 2:
                offenders.append(
                    f"{py.relative_to(REPO)}:{call.lineno} missing user_prompt "
                    f"(args={len(call.args)}, kwargs={sorted(kw)})"
                )
    assert not offenders, "stale generate() signature(s):\n" + "\n".join(offenders)


def test_d543_tier4_invokes_llm_and_parses_response_text():
    """Behavioral: Tier 4 must call generate(system_prompt, user_prompt) and parse
    LLMResponse.text — a 'not relevant' verdict filters the email."""
    from src.ingestion.communications.triage import tier4_llm
    from src.ingestion.models import CommunicationEvent
    from src.shared.llm_provider import LLMResponse

    provider = AsyncMock()
    provider.generate = AsyncMock(
        return_value=LLMResponse(text='{"relevant": false, "rationale_band": "low"}')
    )
    ev = CommunicationEvent(
        source_id=__import__("uuid").uuid4(), message_id="<m@x>",
        sender_email="a@x.example", source_type="eml", body_plain="some body text",
    )
    from src.ingestion.communications.triage.config import load_triage_config
    import asyncio
    tier4_llm._accumulated_cost_usd = 0.0  # reset module cost gate
    cfg = load_triage_config(REPO / "config" / "triage_rules.yaml")
    outcome = asyncio.run(tier4_llm.run_tier4(ev, provider, cfg))

    # generate must have been awaited with a user_prompt (not the stale signature)
    _, kwargs = provider.generate.call_args
    assert "user_prompt" in kwargs and kwargs["user_prompt"], "must pass user_prompt"
    # a not-relevant verdict filters the email at T4
    assert outcome == "filtered_t4_not_organizationally_relevant", f"unexpected: {outcome}"
