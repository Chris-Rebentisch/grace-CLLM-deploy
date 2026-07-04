"""D293 — EvidenceCriterion compile orchestrator tests (mocked LLM + ArcadeDB)."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from src.change_directives.evidence_criterion import (
    CompileResult,
    compile_evidence_criterion,
)
from src.shared.llm_provider import LLMProvider, LLMResponse


class _MockProvider(LLMProvider):
    def __init__(self, text: str) -> None:
        self._text = text

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        json_mode: bool = True,
    ) -> LLMResponse:
        return LLMResponse(text=self._text, provider="mock")

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        # D444 grammar-constrained decoding — not exercised by these tests;
        # return same text as generate() to satisfy the abstract method.
        return LLMResponse(text=self._text, provider="mock")

    async def generate_vision(
        self,
        prompt: str,
        images: list[bytes],
        response_model=None,
    ) -> LLMResponse:
        return LLMResponse(text=self._text, provider="mock")

    async def health_check(self) -> dict:
        return {"ok": True}

    @property
    def provider_name(self) -> str:
        return "mock"


def _ok_explain_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"result": [{"plan": "ok"}]})


def _bad_explain_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(400, json={"error": "syntax error near MATCH"})


@pytest.mark.asyncio
async def test_compile_happy_path_returns_proposed_with_query() -> None:
    provider = _MockProvider(
        json.dumps({"compiled_query": "MATCH (n:Legal_Entity) RETURN n"})
    )
    transport = httpx.MockTransport(_ok_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "Count Legal_Entity nodes",
            {"types": ["Legal_Entity"]},
            provider,
            explain_client=client,
        )
    assert isinstance(result, CompileResult)
    assert result.compiled_query == "MATCH (n:Legal_Entity) RETURN n"
    assert result.compilation_status == "proposed"
    assert result.error_detail is None


@pytest.mark.asyncio
async def test_compile_syntactic_failure_returns_error_detail() -> None:
    provider = _MockProvider(
        json.dumps({"compiled_query": "MATCH (n WITHOUT_CLOSE"})
    )
    transport = httpx.MockTransport(_bad_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "broken nl",
            {},
            provider,
            explain_client=client,
        )
    assert result.compilation_status == "proposed"
    assert result.compiled_query == "MATCH (n WITHOUT_CLOSE"
    assert result.error_detail is not None
    assert "syntactic" in result.error_detail


@pytest.mark.asyncio
async def test_compile_semantic_failure_returns_error_detail() -> None:
    """Stage 1 succeeds, Stage 2 fails."""
    provider = _MockProvider(
        json.dumps({"compiled_query": "MATCH (n:NonExistent) RETURN n"})
    )
    state = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if state["calls"] == 1:
            return httpx.Response(200, json={"result": [{"plan": "ok"}]})
        return httpx.Response(
            500, json={"error": "execution-plan: type not found"}
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "Find non-existent",
            {},
            provider,
            explain_client=client,
        )
    assert result.compilation_status == "proposed"
    assert "semantic" in (result.error_detail or "")


@pytest.mark.asyncio
async def test_compile_llm_returns_garbage_falls_back_to_manual() -> None:
    provider = _MockProvider("not even json")
    transport = httpx.MockTransport(_ok_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "anything",
            {},
            provider,
            explain_client=client,
        )
    assert result.compiled_query is None
    assert result.compilation_status == "proposed"
    assert result.error_detail == "llm_returned_no_compiled_query"


@pytest.mark.asyncio
async def test_compile_result_shape_validates() -> None:
    provider = _MockProvider(json.dumps({"compiled_query": "MATCH (n) RETURN n"}))
    transport = httpx.MockTransport(_ok_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "all", {}, provider, explain_client=client
        )
    dumped = result.model_dump()
    assert set(dumped.keys()) == {
        "compiled_query",
        "compilation_status",
        "error_detail",
    }


# --- F-0047c / ISS-0054: schema-grounded compilation ---


_SEGMENT_SCHEMA = {
    "entity_types": {
        "Legal_Entity": {"description": "A legal entity"},
        "Insurance_Policy": {"description": "A policy"},
    },
    "relationships": {
        "participates_in": {"description": "participation edge"},
    },
}


def test_prompt_includes_schema_vocabulary_legend() -> None:
    """Compiler given schema vocab includes it in the prompt (F-0047c)."""
    from src.change_directives.evidence_criterion import _build_user_prompt

    prompt = _build_user_prompt("Count legal entities", _SEGMENT_SCHEMA)
    assert "Allowed node labels" in prompt
    assert "Legal_Entity" in prompt
    assert "Insurance_Policy" in prompt
    assert "Allowed relationship types" in prompt
    assert "participates_in" in prompt


def test_extract_cypher_vocabulary_labels_and_rels() -> None:
    from src.change_directives.evidence_criterion import extract_cypher_vocabulary

    labels, rels = extract_cypher_vocabulary(
        "MATCH (e:Legal_Entity)-[:participates_in]->(f:Funding_Round) "
        "MATCH (:Zoning)<-[r:has_zoning]-(p) RETURN e, f"
    )
    assert labels == {"Legal_Entity", "Funding_Round", "Zoning"}
    assert rels == {"participates_in", "has_zoning"}


@pytest.mark.asyncio
async def test_off_schema_output_degrades_with_named_tokens() -> None:
    """Off-schema Cypher -> proposed + error_detail naming the tokens (F-0047c)."""
    provider = _MockProvider(
        json.dumps(
            {"compiled_query": "MATCH (p:Property)-[:has_zoning]->(z:Zoning) RETURN z"}
        )
    )
    transport = httpx.MockTransport(_ok_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "Which properties have zoning?",
            _SEGMENT_SCHEMA,
            provider,
            explain_client=client,
        )
    assert result.compilation_status == "proposed"
    assert result.compiled_query is not None
    assert result.error_detail is not None
    assert "off_schema_tokens" in result.error_detail
    assert "Zoning" in result.error_detail
    assert "Property" in result.error_detail
    assert "has_zoning" in result.error_detail


@pytest.mark.asyncio
async def test_on_schema_output_passes_vocabulary_check() -> None:
    provider = _MockProvider(
        json.dumps(
            {
                "compiled_query": (
                    "MATCH (e:Legal_Entity)-[:participates_in]->"
                    "(p:Insurance_Policy) RETURN count(e) AS c"
                )
            }
        )
    )
    transport = httpx.MockTransport(_ok_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "Count entities participating in policies",
            _SEGMENT_SCHEMA,
            provider,
            explain_client=client,
        )
    assert result.compilation_status == "proposed"
    assert result.error_detail is None
    assert result.compiled_query is not None


@pytest.mark.asyncio
async def test_empty_schema_skips_vocabulary_enforcement() -> None:
    """Empty vocab means 'unknown schema', not 'everything is off-schema'."""
    provider = _MockProvider(
        json.dumps({"compiled_query": "MATCH (n:Anything) RETURN n"})
    )
    transport = httpx.MockTransport(_ok_explain_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await compile_evidence_criterion(
            "anything", {}, provider, explain_client=client
        )
    assert result.error_detail is None


def test_ratified_segment_schema_compacts_active_version() -> None:
    """Route helper feeds ratified vocabulary to the compiler (F-0047c)."""
    from unittest.mock import MagicMock, patch

    from src.change_directives.routes import _ratified_segment_schema

    active = MagicMock()
    active.schema_json = {
        "entity_types": {"Legal_Entity": {"description": "A legal entity"}},
        "relationships": {"participates_in": {"description": "edge"}},
    }
    with patch(
        "src.ontology.database.get_active_version", return_value=active
    ):
        schema = _ratified_segment_schema(MagicMock())
    assert schema["entity_types"] == {
        "Legal_Entity": {"description": "A legal entity"}
    }
    assert schema["relationships"] == {"participates_in": {"description": "edge"}}


def test_ratified_segment_schema_no_active_version_returns_empty() -> None:
    from unittest.mock import MagicMock, patch

    from src.change_directives.routes import _ratified_segment_schema

    with patch("src.ontology.database.get_active_version", return_value=None):
        assert _ratified_segment_schema(MagicMock()) == {}
