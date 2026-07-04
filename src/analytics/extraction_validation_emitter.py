"""Emitter for ``grace_extraction_validation_failures_total`` (D162/D242).

Wraps the OTel counter with a Top-N + ``_other_`` cardinality guard on
the ``entity_type`` label so high-cardinality user inputs don't blow up
Prometheus storage. Module-global state holds, per ``ontology_module``,
a count of every entity_type seen; once more than ``TOP_N`` distinct
types appear within a module, tail values bucket into ``_other_``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock

from src.analytics import metrics as grace_metrics

_OTHER = "_other_"
TOP_N = 50

_lock = Lock()
# Per-module entity_type frequency table. Module-global so the Top-N set
# is stable across calls within a process; reset on process restart.
_module_entity_counts: dict[str, Counter[str]] = defaultdict(Counter)


def _bucket_entity_type(ontology_module: str, entity_type: str) -> str:
    """Return either the original entity_type (if in current Top-N set) or ``_other_``."""
    with _lock:
        counts = _module_entity_counts[ontology_module]
        counts[entity_type] += 1
        if len(counts) <= TOP_N:
            return entity_type
        # Outside Top-N? Compute the head set and decide.
        head = {name for name, _ in counts.most_common(TOP_N)}
        return entity_type if entity_type in head else _OTHER


def emit_validation_failure(
    *,
    kind: str,
    ontology_module: str,
    entity_type: str,
) -> None:
    """Increment the validation-failure counter with a cardinality guard.

    ``kind`` is the constraint-validator rule name (e.g.
    ``invalid_entity_type``, ``domain_violation``). ``ontology_module``
    is the module the claim belongs to (or ``"__global__"`` if no
    module). ``entity_type`` is the claim's ``entity_type`` (or
    ``"__none__"`` for relationship-only claims) — bucketed into Top-N or
    ``_other_``.
    """
    bucketed = _bucket_entity_type(ontology_module, entity_type)
    grace_metrics.extraction_validation_failures.add(
        1,
        attributes={
            "kind": kind,
            "ontology_module": ontology_module,
            "entity_type": bucketed,
        },
    )


def reset_for_tests() -> None:
    """Test hook: clear the Top-N tracker."""
    with _lock:
        _module_entity_counts.clear()
