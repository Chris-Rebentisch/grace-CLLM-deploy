"""GrACE Cross-Module Correlation Engine (Chunk 33, D248).

Five fixed pattern detectors (D250) read upstream signals from the
``analytics_signals`` table and a curated raw-Prometheus allowlist
(D252), emit ``DiagnosticRecord`` rows into ``diagnostic_records``,
and surface a per-pattern strength gauge to Prometheus. The
orchestrator persists a ``correlation_runs`` row for each invocation.

Public surface:
- ``DiagnosticRecord`` / ``CorrelationRun`` Pydantic models
- ``CorrelationDetector`` ABC and ``CorrelationRunContext`` carrier
- ``CorrelationEngineConfig`` + ``load_config`` for YAML config
- ``DiagnosticRecordIdempotencyError`` + ``write_run`` writer

Detectors live in ``correlation_engine.patterns``. CLI lives in
``cli``.
"""

from src.analytics.correlation_engine.base import (
    CorrelationDetector,
    CorrelationRun,
    CorrelationRunContext,
    DiagnosticRecord,
)
from src.analytics.correlation_engine.config import (
    CorrelationEngineConfig,
    load_config,
)
from src.analytics.correlation_engine.correlation_record_writer import (
    DiagnosticRecordIdempotencyError,
    write_run,
)
from src.analytics.correlation_engine.orchestrator import (
    default_detectors,
    make_default_context,
    run_all,
)

__all__ = [
    "CorrelationDetector",
    "CorrelationRun",
    "CorrelationRunContext",
    "DiagnosticRecord",
    "CorrelationEngineConfig",
    "load_config",
    "DiagnosticRecordIdempotencyError",
    "write_run",
    "default_detectors",
    "make_default_context",
    "run_all",
]
