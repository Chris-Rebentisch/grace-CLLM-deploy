"""GrACE Signal Computation Pipeline (Chunk 32, D240).

Six named signals (A–F) operationalize Phase-5 "ontology drift"
telemetry: each detector turns upstream data already flowing through
Prometheus and PostgreSQL into normalized [0, 1] strength records,
persists them to ``analytics_signals``, and emits a Grafana gauge.

Public surface:
- ``SignalRecord`` / ``SignalRun`` Pydantic models
- ``SignalDetector`` ABC and ``SignalRunContext`` carrier
- ``SignalPipelineConfig`` + ``load_config`` for YAML config
- ``run_all`` orchestrator entry point
- ``write_run`` writer for run + records

Detectors live in ``signal_pipeline.signals``. CLI lives in ``cli``.
"""

from src.analytics.signal_pipeline.base import (
    SignalDetector,
    SignalRecord,
    SignalRun,
    SignalRunContext,
)
from src.analytics.signal_pipeline.config import (
    SignalPipelineConfig,
    load_config,
)
from src.analytics.prometheus_reader import (
    PrometheusQueryError,
    PrometheusReader,
    PromMatrixResult,
    PromVectorResult,
)
from src.analytics.signal_pipeline.orchestrator import (
    make_default_context,
    run_all,
)
from src.analytics.signal_pipeline.signal_record_writer import (
    SignalRecordIdempotencyError,
    write_run,
)

__all__ = [
    "SignalDetector",
    "SignalRecord",
    "SignalRun",
    "SignalRunContext",
    "SignalPipelineConfig",
    "load_config",
    "PrometheusReader",
    "PrometheusQueryError",
    "PromMatrixResult",
    "PromVectorResult",
    "SignalRecordIdempotencyError",
    "write_run",
    "run_all",
    "make_default_context",
]
