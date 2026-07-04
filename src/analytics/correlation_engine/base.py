"""Correlation engine base classes and Pydantic record models (D248, D250).

Defines:
- ``DiagnosticRecord`` — single emitted correlation observation.
- ``CorrelationRun`` — orchestrator-level run row.
- ``CorrelationRunContext`` — frozen dataclass passed to every detector.
- ``CorrelationDetector`` — abstract base for the five detectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import sessionmaker

    from src.analytics.correlation_engine.config import CorrelationEngineConfig
    from src.analytics.prometheus_reader import PrometheusReader


PatternNameLiteral = Literal[
    "extraction_quality_problem",
    "graph_or_index_problem",
    "schema_drift_per_module",
    "cq_regression_pre_extraction",
    "relationship_gap_propagation",
    # D535 (amends D250): sixth pattern. Surfaced by the A4 correlation
    # probe — domain/range violation (E) + missing edge (B) same module.
    "ontology_constraint_conflict",
]
RootCauseModuleLiteral = Literal[
    "extraction", "retrieval", "graph", "ontology", "discovery"
]
RunStatusLiteral = Literal["running", "success", "partial_failure", "error"]


class DiagnosticRecord(BaseModel):
    """One emitted diagnostic observation (a row in ``diagnostic_records``)."""

    id: UUID = Field(default_factory=uuid4)
    run_id: UUID
    pattern_name: PatternNameLiteral
    ontology_module: str = "__global__"
    suspected_root_cause_module: RootCauseModuleLiteral
    correlation_strength: float = Field(ge=0.0, le=1.0)
    contributing_signals: list[dict[str, Any]] = Field(default_factory=list)
    evidence_snapshot: dict[str, Any]
    human_summary: str = Field(max_length=240)
    detected_at: datetime


class CorrelationRun(BaseModel):
    """One orchestrator invocation (a row in ``correlation_runs``)."""

    id: UUID
    started_at: datetime
    completed_at: datetime | None
    status: RunStatusLiteral
    triggered_by: Literal["cli"] = "cli"
    config_hash: str


@dataclass(frozen=True)
class CorrelationRunContext:
    """Per-run carrier passed to every detector."""

    run_id: UUID
    started_at: datetime
    prometheus_reader: "PrometheusReader"
    session_factory: "sessionmaker"
    config: "CorrelationEngineConfig"
    target_ontology_modules: list[str] | None = None


class CorrelationDetector(ABC):
    """Abstract base for correlation pattern detectors.

    Each concrete detector sets ``pattern_name`` and
    ``suspected_root_cause_module`` (ClassVars) and implements
    ``detect``. The base contract: each detector returns zero or more
    ``DiagnosticRecord`` instances; ``correlation_strength`` MUST be in
    ``[0, 1]``; ``contributing_signals`` MUST be non-empty when a record
    is emitted; ``evidence_snapshot`` MUST be JSON-serializable; and
    ``human_summary`` MUST be ≤ 240 characters.
    """

    pattern_name: ClassVar[PatternNameLiteral]
    suspected_root_cause_module: ClassVar[RootCauseModuleLiteral]

    @abstractmethod
    async def detect(
        self, run_context: CorrelationRunContext
    ) -> list[DiagnosticRecord]:
        """Compute diagnostic records for this pattern."""
        raise NotImplementedError
