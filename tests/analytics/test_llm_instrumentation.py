"""Tests for `record_llm_call` (spec §6, §10.3)."""

from __future__ import annotations

import pytest

from src.analytics.llm_instrumentation import record_llm_call


@pytest.mark.asyncio
async def test_record_llm_call_success_sets_expected_span_attributes(span_exporter):
    async with record_llm_call(
        system="ollama",
        model="qwen2.5:7b",
        grace_module="extraction",
        grace_operation="extract",
    ) as ctx:
        ctx.set_input_tokens(100)
        ctx.set_output_tokens(250)
        ctx.set_finish_reason("stop")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1, f"expected 1 span, got {len(spans)}"
    span = spans[0]

    assert span.name == "gen_ai.call"
    assert span.attributes["gen_ai.system"] == "ollama"
    assert span.attributes["gen_ai.request.model"] == "qwen2.5:7b"
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.attributes["grace.module"] == "extraction"
    assert span.attributes["grace.operation"] == "extract"
    assert span.attributes["gen_ai.usage.input_tokens"] == 100
    assert span.attributes["gen_ai.usage.output_tokens"] == 250
    assert span.attributes["gen_ai.response.finish_reasons"] == ("stop",)
    assert span.status.status_code.name == "UNSET"


@pytest.mark.asyncio
async def test_record_llm_call_error_path_tags_span_and_reraises(span_exporter):
    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with record_llm_call(
            system="anthropic",
            model="claude-haiku-4-5-20251001",
            grace_module="regeneration",
            grace_operation="synthesize",
        ):
            raise _BoomError("provider timeout")

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]

    assert span.status.status_code.name == "ERROR"
    assert span.attributes["gen_ai.system"] == "anthropic"
    assert span.attributes["grace.module"] == "regeneration"
    # Exception was recorded as an event with the type name.
    event_names = [event.name for event in span.events]
    assert "exception" in event_names
