"""API endpoints for the Regeneration Module (§9 of chunk-23-spec.md).

Singleton pipeline pattern mirrors src/api/retrieval_routes.py.
Error mapping (D135): retrieve→503, assemble→500, synthesize→502.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.api.retrieval_routes import _get_pipeline as _get_retrieval_pipeline
from src.regeneration.regeneration_config import (
    RegenSettings,
    get_regen_settings,
)
from src.regeneration.regeneration_models import (
    RegenerationConfigResponse,
    RegenerationError,
    RegenerationQuery,
    RegenerationResponse,
)
from src.regeneration.regeneration_pipeline import (
    AssembleStageError,
    PromptAssemblyError,
    RegenerationPipeline,
    RetrievalStageError,
    SynthesizeStageError,
)
from src.shared.llm_provider import read_llm_config_from_yaml

logger = structlog.get_logger()

router = APIRouter(prefix="/api/regeneration", tags=["regeneration"])

_pipeline: RegenerationPipeline | None = None


def _get_pipeline() -> RegenerationPipeline:
    global _pipeline
    if _pipeline is None:
        retrieval_pipeline = _get_retrieval_pipeline()
        _pipeline = RegenerationPipeline(
            retrieval_pipeline=retrieval_pipeline,
            settings=get_regen_settings(),
        )
    return _pipeline


def reset_pipeline_singleton() -> None:
    """Test helper — reset the module singleton."""
    global _pipeline
    _pipeline = None


def _error_response(
    status_code: int,
    stage: str,
    exc: Exception,
    request_id: str,
    stage_latencies: dict[str, float] | None = None,
) -> JSONResponse:
    body = RegenerationError(
        stage=stage,  # type: ignore[arg-type]
        error_type=type(exc).__name__,
        error_message=str(exc),
        partial_response=None,
        request_id=request_id,
        stage_latencies_ms=dict(stage_latencies or {}),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


@router.post("/query", response_model=RegenerationResponse)
async def regeneration_query(query: RegenerationQuery):  # type: ignore[return-value]
    pipeline = _get_pipeline()
    request_id = str(uuid.uuid4())
    try:
        return await pipeline.regenerate(query)
    except RetrievalStageError as exc:
        logger.error(
            "regeneration.route.retrieve_failed",
            request_id=request_id,
            error=str(exc),
        )
        return _error_response(
            503, "retrieve", exc, request_id, exc.stage_latencies
        )
    except (PromptAssemblyError, AssembleStageError) as exc:
        logger.error(
            "regeneration.route.assemble_failed",
            request_id=request_id,
            error=str(exc),
        )
        return _error_response(
            500, "assemble", exc, request_id, exc.stage_latencies
        )
    except SynthesizeStageError as exc:
        logger.error(
            "regeneration.route.synthesize_failed",
            request_id=request_id,
            error=str(exc),
        )
        return _error_response(
            502, "synthesize", exc, request_id, exc.stage_latencies
        )


@router.get(
    "/config", response_model=RegenerationConfigResponse
)
async def regeneration_config() -> RegenerationConfigResponse:
    settings = get_regen_settings()
    return _render_config(settings)


def _render_config(settings: RegenSettings) -> RegenerationConfigResponse:
    defaults = RegenSettings()
    phase_keys = (
        "prepare", "open", "structure", "clarify", "close", "none"
    )
    overridden: list[str] = []
    for key in phase_keys:
        attr = f"phase_style_{key}"
        if getattr(settings, attr) != getattr(defaults, attr):
            overridden.append(key)
    # Report the model that synthesis ACTUALLY uses. RegenSettings still
    # carries a vestigial regeneration_model default ("qwen2.5:7b"), but
    # ResponseSynthesizer dispatches through get_provider(), which resolves
    # provider/model from config/discovery.yaml. src/regeneration/ is under
    # the D193 hard lock (scripts/check-regeneration-unchanged.sh), so the
    # fix lives here at the route layer: same field name (API compat), value
    # sourced from the resolved llm config.
    resolved_llm = read_llm_config_from_yaml()
    return RegenerationConfigResponse(
        system_budget_tokens=settings.system_budget_tokens,
        context_budget_tokens=settings.context_budget_tokens,
        query_budget_tokens=settings.query_budget_tokens,
        response_budget_tokens=settings.response_budget_tokens,
        total_input_budget_tokens=settings.total_input_budget_tokens,
        regeneration_model=resolved_llm["model"],
        regeneration_temperature=settings.regeneration_temperature,
        chars_per_token=settings.chars_per_token,
        enable_claim_span_detection=settings.enable_claim_span_detection,
        span_detector_mode=settings.span_detector_mode,  # type: ignore[arg-type]
        phase_style_overrides_applied=overridden,
    )
