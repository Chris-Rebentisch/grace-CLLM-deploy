"""MINE retention metric emitter (Chunk 34, D260).

Bridges ``MINESampler`` results into the OTel observable gauge
``grace_mine_retention_ratio`` via the in-process registry held in
``src.analytics.metrics``. The gauge's callback reads the registry at
scrape time, so emitter writes must be cheap and non-blocking.

Key design rules (D260, D162, D246):

* The emitter stores ``ontology_module`` as the latest-batch label and
  ``schema_version_id`` as the per-batch discriminator. Together they
  serve as a substitute for ``sample_batch_id`` — the
  ``mine_samples.schema_version_id`` column is the schema-promotion key
  of the batch (see ``MINESampler.sample_document``).
* Top-N + ``_other_`` cardinality cap (D162). The default cap is 10
  distinct ``ontology_module`` values per spec §6 CP3 step 1d; everything
  beyond rolls up to the literal label ``_other_``.
* CLI / API only (D246). The emitter must NOT be invoked from inside
  any in-process scheduler.
* Confidence numerals are not surfaced through the API. The retention
  ratio is a float in [0, 1] persisted as a metric only.
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import structlog

from src.analytics import metrics as _metrics

logger = structlog.get_logger()


_DEFAULT_CARDINALITY_CAP = 10
_OTHER_LABEL = "_other_"
_lock = threading.Lock()


def set_mine_retention_observation(
    *,
    ontology_module: str,
    schema_version_id: str,
    retention_ratio: float,
    cardinality_cap: int = _DEFAULT_CARDINALITY_CAP,
) -> None:
    """Record a MINE retention observation for a given (module, schema_version)
    pair, applying D162 cardinality capping.

    Args:
        ontology_module: Active ontology module name. Top-N + ``_other_``
            capped to ``cardinality_cap`` distinct values.
        schema_version_id: Stringified UUID of the schema-version-id batch.
        retention_ratio: Float in [0, 1]; clamped on write.
        cardinality_cap: Maximum distinct ``ontology_module`` labels
            tracked before falling back to ``_other_``. Default 10 per
            spec §6 CP3 step 1d (D162).

    Idempotent on (ontology_module, schema_version_id): repeated writes
    overwrite the prior value.
    """
    if not isinstance(retention_ratio, (int, float)):
        raise TypeError(
            f"retention_ratio must be numeric, got {type(retention_ratio)!r}"
        )
    if retention_ratio < 0.0:
        retention_ratio = 0.0
    elif retention_ratio > 1.0:
        retention_ratio = 1.0

    if not ontology_module:
        ontology_module = "unknown"
    if not schema_version_id:
        schema_version_id = "unknown"

    with _lock:
        registry = _metrics._MINE_RETENTION_OBSERVATIONS  # noqa: SLF001
        existing_modules = {key[0] for key in registry}

        if ontology_module not in existing_modules and len(existing_modules) >= cardinality_cap:
            label = _OTHER_LABEL
        else:
            label = ontology_module

        registry[(label, schema_version_id)] = float(retention_ratio)

        logger.debug(
            "mine.emitter.observation_set",
            ontology_module=label,
            schema_version_id=schema_version_id,
            retention_ratio=float(retention_ratio),
            tracked_modules=len({k[0] for k in registry}),
        )


def reset_mine_retention_observations() -> None:
    """Clear the in-process registry. Test-only helper."""
    with _lock:
        _metrics._MINE_RETENTION_OBSERVATIONS.clear()  # noqa: SLF001


def snapshot_mine_retention_observations() -> dict[tuple[str, str], float]:
    """Return a copy of the current registry. Test/debug helper."""
    with _lock:
        return dict(_metrics._MINE_RETENTION_OBSERVATIONS)  # noqa: SLF001


def latest_module_observations() -> "OrderedDict[str, float]":
    """Return a flat module->ratio view (last write wins per module).

    Useful for dashboards that prefer module-level rollup; the canonical
    label set used by Prometheus exposition is the (module,
    schema_version_id) pair.
    """
    with _lock:
        flat: "OrderedDict[str, float]" = OrderedDict()
        for (module, _schema_version_id), value in _metrics._MINE_RETENTION_OBSERVATIONS.items():  # noqa: SLF001
            flat[module] = value
        return flat
