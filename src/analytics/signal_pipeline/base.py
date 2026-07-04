"""Signal pipeline base classes and Pydantic record models (D240, D245).

Defines:
- ``SignalRecord`` — single emitted signal observation.
- ``SignalRun`` — orchestrator-level run row.
- ``SignalRunContext`` — frozen dataclass passed to every detector. Carries
  a ``session_factory`` (NOT a session instance) so each detector can open
  its own session under ``asyncio.gather`` without cross-detector session
  contention (R2 mitigation).
- ``SignalDetector`` — abstract base for the six detectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import sessionmaker

    from src.analytics.signal_pipeline.config import SignalPipelineConfig
    from src.analytics.prometheus_reader import PrometheusReader


SignalTypeLiteral = Literal["A", "B", "C", "D", "E", "F"]
RunStatusLiteral = Literal["running", "success", "partial_failure", "error"]


class SignalRecord(BaseModel):
    """One emitted signal observation (a row in ``analytics_signals``)."""

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    signal_type: SignalTypeLiteral
    ontology_module: str = "__global__"
    strength: float = Field(ge=0.0, le=1.0)
    evidence_snapshot: dict
    detected_at: datetime


class SignalRun(BaseModel):
    """One orchestrator invocation (a row in ``signal_runs``)."""

    id: UUID
    started_at: datetime
    completed_at: datetime | None
    status: RunStatusLiteral
    triggered_by: Literal["cli"] = "cli"
    config_hash: str


@dataclass(frozen=True)
class SignalRunContext:
    """Per-run carrier passed to every detector.

    ``session_factory`` is a ``sqlalchemy.orm.sessionmaker``; each
    detector opens (and closes) its own session. Sharing a session across
    coroutines under ``asyncio.gather`` is unsafe.
    """

    run_id: UUID
    started_at: datetime
    prometheus_reader: "PrometheusReader"
    session_factory: "sessionmaker"
    config: "SignalPipelineConfig"
    target_ontology_modules: list[str] | None = None
    # Mutable per-run scratchpad (the dataclass is frozen; the dict is not).
    # Detectors record why they no-op'd (e.g. missing Prometheus history) under
    # diagnostics["prerequisites_not_met"][signal_type] so the CLI summary can
    # distinguish "prerequisites not met" from "ran, found nothing" (C1 follow-up:
    # silent detector no-ops on cold Prometheus were indistinguishable from a
    # healthy quiet corpus).
    diagnostics: dict = field(default_factory=dict)


def note_prerequisites_not_met(
    run_context: SignalRunContext, signal_type: str, missing: list[str]
) -> None:
    """Record a detector no-op caused by missing prerequisites (see
    ``SignalRunContext.diagnostics``). Detection logic is unchanged — this is
    visibility only."""
    run_context.diagnostics.setdefault("prerequisites_not_met", {})[signal_type] = list(
        missing
    )


class SignalDetector(ABC):
    """Abstract base. Every concrete detector sets ``signal_type`` and
    implements ``detect``."""

    signal_type: ClassVar[SignalTypeLiteral]

    @abstractmethod
    async def detect(self, run_context: SignalRunContext) -> list[SignalRecord]:
        """Compute strength records for this signal."""
        raise NotImplementedError
