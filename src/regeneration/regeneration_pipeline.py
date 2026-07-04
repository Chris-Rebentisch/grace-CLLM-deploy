"""RegenerationPipeline — orchestration across retrieve/assemble/synthesize/span_detect.

§8 of chunk-23-spec.md. Typed stage exceptions carry partial
stage_latencies for the route layer to include in RegenerationError
response bodies. D135: internal typed exceptions; route layer is the
single translation point to HTTP. D136: prompt logging gated by
settings.debug_log_prompts.
"""

from __future__ import annotations

import hashlib
import time
import uuid

import structlog
from opentelemetry import trace

from src.analytics.llm_instrumentation import grace_call_tags
from src.analytics.pipeline_instrumentation import record_pipeline_stage
from src.regeneration.claim_span_detector import ClaimSpanDetector
from src.regeneration.prompt_assembly import (
    PromptAssembler,
    PromptAssemblyError as _AssemblyError,
)
from src.regeneration.regeneration_config import RegenSettings
from src.regeneration.regeneration_models import (
    RegenerationQuery,
    RegenerationResponse,
    ResponseMetadata,
)
from src.regeneration.response_synthesizer import ResponseSynthesizer
from src.retrieval.pipeline import RetrievalPipeline
from src.retrieval.retrieval_models import RetrievalQuery

logger = structlog.get_logger()
_tracer = trace.get_tracer("grace.regeneration.pipeline")


class RegenerationStageError(Exception):
    """Base class for regeneration stage failures.

    Carries partial per-stage latencies so the route layer can merge
    them into the RegenerationError response body.
    """

    stage: str = "unknown"

    def __init__(
        self,
        message: str,
        stage_latencies: dict[str, float] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage_latencies = dict(stage_latencies) if stage_latencies else {}


class RetrievalStageError(RegenerationStageError):
    stage = "retrieve"


class AssembleStageError(RegenerationStageError):
    stage = "assemble"


class SynthesizeStageError(RegenerationStageError):
    stage = "synthesize"


class PromptAssemblyError(AssembleStageError):
    """System + query alone exceeded budget — operator config issue."""


def _hash_prompt(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class RegenerationPipeline:
    """Orchestrate retrieve → assemble → synthesize → span_detect."""

    def __init__(
        self,
        retrieval_pipeline: RetrievalPipeline,
        settings: RegenSettings,
    ) -> None:
        self.retrieval_pipeline = retrieval_pipeline
        self.settings = settings
        self.assembler = PromptAssembler(settings)
        self.synthesizer = ResponseSynthesizer(settings)
        self.span_detector = ClaimSpanDetector(settings)

    async def regenerate(
        self, query: RegenerationQuery
    ) -> RegenerationResponse:
      with _tracer.start_as_current_span("regeneration.run") as _outer_span:
        _outer_span.set_attribute("grace.module", "regeneration")
        _outer_span.set_attribute("grace.pipeline", "regeneration")
        request_id = str(uuid.uuid4())
        latency_ms: dict[str, float] = {}

        # Stage 1: retrieve
        t0 = time.perf_counter()
        try:
            async with record_pipeline_stage(
                pipeline="regeneration", stage="retrieve"
            ):
                retrieval_query = (
                    query.retrieval_query
                    if query.retrieval_query is not None
                    else RetrievalQuery(query_text=query.query_text, top_k=10)
                )
                retrieval_response = await self.retrieval_pipeline.query(
                    retrieval_query
                )
        except Exception as exc:  # noqa: BLE001
            latency_ms["retrieve"] = (time.perf_counter() - t0) * 1000
            raise RetrievalStageError(
                str(exc), stage_latencies=latency_ms
            ) from exc
        latency_ms["retrieve"] = (time.perf_counter() - t0) * 1000

        # Stage 2: assemble
        t0 = time.perf_counter()
        try:
            async with record_pipeline_stage(
                pipeline="regeneration", stage="assemble"
            ):
                assembled = self.assembler.assemble(query, retrieval_response)
        except _AssemblyError as exc:
            latency_ms["assemble"] = (time.perf_counter() - t0) * 1000
            raise PromptAssemblyError(
                str(exc), stage_latencies=latency_ms
            ) from exc
        except Exception as exc:  # noqa: BLE001
            latency_ms["assemble"] = (time.perf_counter() - t0) * 1000
            raise AssembleStageError(
                str(exc), stage_latencies=latency_ms
            ) from exc
        latency_ms["assemble"] = (time.perf_counter() - t0) * 1000

        full_prompt = (
            f"{assembled.system_prompt}\n{assembled.context}\n"
            f"{assembled.user_query}"
        )
        if self.settings.debug_log_prompts:
            logger.info(
                "regeneration.assembled_prompt",
                request_id=request_id,
                prompt=full_prompt,
            )
        else:
            logger.info(
                "regeneration.assembled_prompt.meta",
                request_id=request_id,
                prompt_hash=_hash_prompt(full_prompt),
                context_truncated=assembled.context_truncated,
                total_token_estimate=assembled.total_token_estimate,
            )

        # Stage 3: synthesize (wrap in grace_call_tags so provider LLM call
        # inherits grace.module="regeneration", grace.operation="synthesize")
        t0 = time.perf_counter()
        try:
            async with record_pipeline_stage(
                pipeline="regeneration", stage="synthesize"
            ):
                async with grace_call_tags("regeneration", "synthesize"):
                    llm_response = await self.synthesizer.synthesize(
                        assembled, query.overrides
                    )
        except Exception as exc:  # noqa: BLE001
            latency_ms["synthesize"] = (time.perf_counter() - t0) * 1000
            raise SynthesizeStageError(
                str(exc), stage_latencies=latency_ms
            ) from exc
        latency_ms["synthesize"] = (time.perf_counter() - t0) * 1000

        # Stage 4: span_detect (degraded-success — never raises upward)
        claim_spans = []
        span_note: str | None = None
        if self.settings.enable_claim_span_detection:
            t0 = time.perf_counter()
            try:
                async with record_pipeline_stage(
                    pipeline="regeneration", stage="span_detect"
                ):
                    claim_spans, span_note = self.span_detector.detect(
                        llm_response.text, retrieval_response
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "regeneration.span_detect.failed",
                    request_id=request_id,
                    error=str(exc),
                )
                claim_spans = []
                span_note = "span_detection_degraded"
            latency_ms["span_detect"] = (time.perf_counter() - t0) * 1000

        latency_ms["total"] = (
            latency_ms.get("retrieve", 0.0)
            + latency_ms.get("assemble", 0.0)
            + latency_ms.get("synthesize", 0.0)
            + latency_ms.get("span_detect", 0.0)
        )

        contributing_ids: list[str] = []
        seen: set[str] = set()
        for span in claim_spans:
            for gid in span.supporting_grace_ids:
                if gid not in seen:
                    seen.add(gid)
                    contributing_ids.append(gid)

        model_override_applied = False  # D137: never True in v1
        metadata = ResponseMetadata(
            context_truncated=assembled.context_truncated,
            span_detector_mode="sentence_fallback",
            phase_style_applied=assembled.phase_style_applied,
            span_detection_note=span_note,
            model_override_applied=model_override_applied,
        )

        token_usage = {
            "input_tokens": int(getattr(llm_response, "input_tokens", 0) or 0),
            "output_tokens": int(
                getattr(llm_response, "output_tokens", 0) or 0
            ),
            "system_estimate": assembled.system_token_estimate,
            "context_estimate": assembled.context_token_estimate,
        }

        return RegenerationResponse(
            query=query.query_text,
            response_text=llm_response.text,
            claim_spans=claim_spans,
            phase_state=query.phase_state,
            contributing_grace_ids=contributing_ids,
            strategy_contributions=dict(
                retrieval_response.strategy_contributions
            ),
            latency_ms=latency_ms,
            token_usage=token_usage,
            model=getattr(llm_response, "model", "") or "",
            provider=getattr(llm_response, "provider", "") or "",
            retrieval_mode=retrieval_response.retrieval_mode,
            response_metadata=metadata,
        )
