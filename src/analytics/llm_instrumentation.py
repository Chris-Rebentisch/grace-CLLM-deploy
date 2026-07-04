"""LLM call instrumentation (OTel spans + `gen_ai.*` metrics).

Wraps every LLM call site with a `gen_ai.call` span and records the
two metric families from spec §5.2:
`gen_ai_client_operation_duration_seconds` and
`gen_ai_client_token_usage`.

Pipeline wrappers set `grace.module` / `grace.operation` for nested
LLM calls via `grace_call_tags(...)` + ContextVars (D156). Do NOT
read `Span.attributes` — that API is not stable across SDK versions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from typing import AsyncIterator, Literal

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from src.analytics import metrics

_tracer = trace.get_tracer("grace.analytics.llm")


GraceModule = Literal[
    "extraction", "regeneration", "retrieval", "discovery", "ontology"
]
OperationName = Literal["chat", "text_completion"]


_grace_module_cv: ContextVar[str] = ContextVar(
    "_grace_module_cv", default="unknown"
)
_grace_operation_cv: ContextVar[str] = ContextVar(
    "_grace_operation_cv", default="generate"
)


@asynccontextmanager
async def grace_call_tags(module: str, operation: str) -> AsyncIterator[None]:
    """Tag any LLM calls inside this block with a module + operation.

    Providers are long-lived singletons, so we cannot pass these per
    call. ContextVars propagate through `await` and `asyncio.gather`
    boundaries in Python 3.7+ (§7.3).
    """
    m_token = _grace_module_cv.set(module)
    o_token = _grace_operation_cv.set(operation)
    try:
        yield
    finally:
        _grace_module_cv.reset(m_token)
        _grace_operation_cv.reset(o_token)


def _current_grace_module() -> str:
    return _grace_module_cv.get()


def _current_grace_operation() -> str:
    return _grace_operation_cv.get()


@dataclass
class LLMCallContext:
    """Mutable context yielded by `record_llm_call`.

    The provider fills these in as it learns them from its response.
    Anything unset at exit is simply not recorded.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: str | None = None

    def set_input_tokens(self, count: int) -> None:
        self.input_tokens = count

    def set_output_tokens(self, count: int) -> None:
        self.output_tokens = count

    def set_finish_reason(self, reason: str) -> None:
        self.finish_reason = reason


@asynccontextmanager
async def record_llm_call(
    system: Literal["ollama", "anthropic", "openai"],
    model: str,
    grace_module: str,
    grace_operation: str,
    operation_name: OperationName = "chat",
) -> AsyncIterator[LLMCallContext]:
    """Start a `gen_ai.call` span and record duration + token metrics.

    On exception: span status → error, `error_type` label set on the
    duration histogram, exception re-raised.
    """
    ctx = LLMCallContext()
    duration_labels: dict[str, str] = {
        "gen_ai_system": system,
        "gen_ai_request_model": model,
        "gen_ai_operation_name": operation_name,
        "grace_module": grace_module,
        "grace_operation": grace_operation,
    }

    with _tracer.start_as_current_span("gen_ai.call") as span:
        span.set_attribute("gen_ai.system", system)
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("gen_ai.operation.name", operation_name)
        span.set_attribute("grace.module", grace_module)
        span.set_attribute("grace.operation", grace_operation)

        start = monotonic()
        try:
            yield ctx
        except Exception as exc:
            duration_labels["error_type"] = type(exc).__name__
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            raise
        finally:
            duration = monotonic() - start
            metrics.llm_call_duration.record(duration, attributes=duration_labels)

            token_base = {
                "gen_ai_system": system,
                "gen_ai_request_model": model,
                "grace_module": grace_module,
            }
            if ctx.input_tokens is not None:
                span.set_attribute("gen_ai.usage.input_tokens", ctx.input_tokens)
                metrics.llm_token_usage.record(
                    ctx.input_tokens,
                    attributes={**token_base, "gen_ai_token_type": "input"},
                )
            if ctx.output_tokens is not None:
                span.set_attribute("gen_ai.usage.output_tokens", ctx.output_tokens)
                metrics.llm_token_usage.record(
                    ctx.output_tokens,
                    attributes={**token_base, "gen_ai_token_type": "output"},
                )
            if ctx.finish_reason is not None:
                span.set_attribute(
                    "gen_ai.response.finish_reasons", [ctx.finish_reason]
                )
