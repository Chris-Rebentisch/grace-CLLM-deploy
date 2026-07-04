"""Communication Ingestion module (Chunk 55, D419/D420/D427).

Bootstraps GrACE's Communication Ingestion pipeline: Pydantic v2 domain
models, EmailAdapter ABC, file-based adapter implementations, D274 hybrid
readiness gate, single-run CLI pipeline, and API routes.
"""

from src.ingestion.models import (
    AttachmentRef,
    CommunicationEvent,
    ConnectionTestResult,
    IngestionCheckpoint,
    IngestionRunStatus,
    ReadinessResult,
    ReadinessThresholds,
    Recipient,
    SegmentReadiness,
    SourceConfig,
)

__all__ = [
    "AttachmentRef",
    "CommunicationEvent",
    "ConnectionTestResult",
    "IngestionCheckpoint",
    "IngestionRunStatus",
    "ReadinessResult",
    "ReadinessThresholds",
    "Recipient",
    "SegmentReadiness",
    "SourceConfig",
]
