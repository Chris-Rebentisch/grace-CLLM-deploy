"""Pipeline stage instrumentation.

Each GrACE pipeline (extraction / retrieval / regeneration) has an
outer span opened by the pipeline class, and per-stage child spans
opened via `record_pipeline_stage`. On error the stage is counted in
`grace_pipeline_stage_errors_total`; always the duration is recorded
in `grace_pipeline_stage_duration_seconds`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from time import monotonic
from typing import AsyncIterator, Literal

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from src.analytics import metrics

_tracer = trace.get_tracer("grace.analytics.pipeline")


PipelineName = Literal["extraction", "retrieval", "regeneration"]


@asynccontextmanager
async def record_pipeline_stage(
    pipeline: PipelineName,
    stage: str,
) -> AsyncIterator[None]:
    """Open a `{pipeline}.stage.{stage}` span; record duration + error.

    On exception: status → error, `grace_pipeline_stage_errors_total`
    incremented with `error_type`, exception re-raised.
    """
    with _tracer.start_as_current_span(f"{pipeline}.stage.{stage}") as span:
        span.set_attribute("grace.pipeline", pipeline)
        span.set_attribute("grace.stage", stage)

        start = monotonic()
        status = "ok"
        error_type: str | None = None
        try:
            yield
        except Exception as exc:
            status = "error"
            error_type = type(exc).__name__
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            span.record_exception(exc)
            metrics.pipeline_stage_errors.add(
                1,
                attributes={
                    "pipeline": pipeline,
                    "stage": stage,
                    "error_type": error_type,
                },
            )
            raise
        finally:
            duration = monotonic() - start
            metrics.pipeline_stage_duration.record(
                duration,
                attributes={
                    "pipeline": pipeline,
                    "stage": stage,
                    "status": status,
                },
            )
