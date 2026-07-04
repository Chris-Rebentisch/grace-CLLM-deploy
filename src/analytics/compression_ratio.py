"""`grace_compression_ratio` emission helper (Chunk 25 first observation).

Single public function: ``record_compression_ratio``. Writes to the
Chunk 24-registered `grace_compression_ratio` histogram via the OTel
Meter API — does not create any instrument. Zero-denominator and
zero-numerator cases are skipped (never emitted as ``+Inf`` or 0).

See ``docs/chunk-25-spec.md`` §3 for locked semantics.
"""

from __future__ import annotations

import structlog

from src.analytics import metrics as grace_metrics

log = structlog.get_logger(__name__)


def record_compression_ratio(
    source_tokens: int,
    entities: int,
    relationships: int,
    ontology_module: str | None,
    event_id: str | None = None,
    document_id: str | None = None,
) -> None:
    """Emit one ``grace_compression_ratio`` observation.

    Ratio is ``source_tokens / (entities + relationships)`` —
    tokens-per-graph-element. ``ontology_module=None`` is labeled as
    ``"unknown"`` (never ``"_init"``, which is reserved per D151).
    """
    denominator = (entities or 0) + (relationships or 0)
    if denominator <= 0:
        log.info(
            "compression_ratio.skipped_zero_denominator",
            event_id=event_id,
            document_id=document_id,
            entities=entities,
            relationships=relationships,
        )
        return
    if (source_tokens or 0) <= 0:
        log.warning(
            "compression_ratio.skipped_zero_numerator",
            event_id=event_id,
            document_id=document_id,
            source_tokens=source_tokens,
        )
        return

    ratio = source_tokens / denominator
    module_label = ontology_module if ontology_module else "unknown"
    grace_metrics.compression_ratio.record(
        ratio,
        attributes={"ontology_module": module_label},
    )
