"""Cross-Executive Divergence Map computation (Chunk 37, D284).

This module is a thin presentation-layer wrapper around
``src/ontology/diff_engine.py:compute_entity_level_diff`` (D284 reuse;
zero edits to the diff engine). It loads two ratified ontology version
schemas, calls the OM4OV diff, aliases the four buckets to the
Reconciliation vocabulary, joins ArcadeDB instance counts via
``src/ontology/recon_gap_report.py:_read_graph_counts`` (read-only
consumer, zero edits), and persists the result to
``recon_divergence_maps`` (migration ``c37c``, B4 resolution).

Single source of truth for the OM4OV-to-Reconciliation alias:
``OM4OV_TO_RECON_ALIAS``. Inversion note: when ``compute_entity_level_diff``
is called with ``old=version_a`` and ``new=version_b``, the OM4OV
``added`` set corresponds to ``additive_B`` (present in B but not A),
and ``removed`` corresponds to ``additive_A`` (present in A but not B).
The alias dict accounts for this.

Per-bucket entry cap: top 20 entries per bucket sorted by
``instance_count DESC`` (D284 v1 cap; pagination is post-Phase-5.5).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.recon_models import (
    DivergenceMapBucket,
    DivergenceMapEntry,
    DivergenceMapResponse,
)
from src.graph.arcade_client import ArcadeClient
from src.ontology.database import get_version_by_id
from src.ontology.diff_engine import compute_entity_level_diff
from src.ontology.recon_gap_report import _read_graph_counts

logger = structlog.get_logger()


# Single source of truth — D284 §4 alias dict.
OM4OV_TO_RECON_ALIAS: dict[str, str] = {
    "added": "additive_B",
    "removed": "additive_A",
    "modified": "contradictory",
    "unchanged": "consensus",
}

_BUCKET_ORDER = ("additive_A", "additive_B", "contradictory", "consensus")
_PER_BUCKET_CAP = 20


def _entry_name(item) -> str:
    """Extract a name from either a string entry or a {'name': ..., ...} dict."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("name", ""))
    return str(item)


def _build_entries_for_bucket(
    om4ov_bucket_items,
    instance_counts: dict[str, int],
    element_type_label: str,
    source_origins_map: dict[str, list[str]] | None = None,
) -> list[DivergenceMapEntry]:
    """Build ``DivergenceMapEntry`` items for a bucket from the OM4OV
    output, joining per-type instance counts and capping at 20.

    Chunk 59 (D426 — CP7): ``source_origins_map`` carries per-type
    evidence-origin literals (``"document"`` / ``"communication"``).
    Empty list for types with no origin data.
    """
    raw: list[tuple[str, int]] = []
    for item in om4ov_bucket_items or []:
        name = _entry_name(item)
        if not name:
            continue
        raw.append((name, int(instance_counts.get(name, 0))))
    # Sort by instance_count DESC, then name ASC for stable ordering.
    raw.sort(key=lambda pair: (-pair[1], pair[0]))
    capped = raw[:_PER_BUCKET_CAP]
    origins = source_origins_map or {}
    return [
        DivergenceMapEntry(
            element_name=name,
            element_type=element_type_label,
            instance_count=count,
            source_origins=origins.get(name, []),
        )
        for name, count in capped
    ]


async def compute_divergence_map(
    version_a_id: UUID,
    version_b_id: UUID,
    segment_id: str | None,
    arcade_client: ArcadeClient,
    db_session: Session,
) -> DivergenceMapResponse:
    """Compute and persist a Cross-Executive Divergence Map (D284).

    Parameters
    ----------
    version_a_id, version_b_id:
        UUIDs of two ratified ``ontology_versions`` rows. The OM4OV
        diff is called as ``compute_entity_level_diff(old=A, new=B)``.
    segment_id:
        Optional segment identifier for the persisted row + GET-latest
        triple key.
    arcade_client:
        ArcadeDB client for per-type instance count lookup.
    db_session:
        SQLAlchemy session for ``ontology_versions`` reads and the
        ``recon_divergence_maps`` write.
    """
    version_a = get_version_by_id(db_session, version_a_id)
    if version_a is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail=f"version_a not found: {version_a_id}",
        )
    version_b = get_version_by_id(db_session, version_b_id)
    if version_b is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail=f"version_b not found: {version_b_id}",
        )

    # OM4OV diff (zero edits to the diff engine).
    diff = compute_entity_level_diff(
        old_schema=version_a.schema_json,
        new_schema=version_b.schema_json,
    )
    entity_diff = diff.get("entity_types", {}) or {}
    rel_diff = diff.get("relationships", {}) or {}

    # Per-type instance counts (zero edits to recon_gap_report).
    _total, instance_counts = await _read_graph_counts(arcade_client)

    # Chunk 59 (D426 — CP7): per-type evidence-origin lookup.
    # Returns {type_name: ["document", "communication"]} for entries
    # that have vertices with a matching evidence_origin property.
    source_origins_map: dict[str, list[str]] = {}
    try:
        origin_result = await arcade_client.execute_sql(
            "SELECT @type AS type_name, "
            "COALESCE(evidence_origin, 'document') AS origin "
            "FROM V GROUP BY @type, COALESCE(evidence_origin, 'document')"
        )
        origin_rows = (
            origin_result.get("result", [])
            if isinstance(origin_result, dict)
            else []
        )
        for r in origin_rows:
            if isinstance(r, dict):
                tn = str(r.get("type_name", ""))
                orig = str(r.get("origin", ""))
                if tn and orig in ("document", "communication"):
                    source_origins_map.setdefault(tn, [])
                    if orig not in source_origins_map[tn]:
                        source_origins_map[tn].append(orig)
    except Exception:  # noqa: BLE001
        # Graceful degradation — origin query optional.
        pass

    # Build the four buckets. We use the alias dict as single source of
    # truth and pull both entity-type and relationship contributions
    # through the same pipeline (relationships will frequently have a
    # zero instance_count because the Arcade query is type-bucketed at
    # the V family; that is acceptable in v1).
    buckets_payload: dict[str, list[DivergenceMapEntry]] = {
        b: [] for b in _BUCKET_ORDER
    }
    for om4ov_key, recon_bucket in OM4OV_TO_RECON_ALIAS.items():
        entity_entries = _build_entries_for_bucket(
            entity_diff.get(om4ov_key, []),
            instance_counts,
            "entity_type",
            source_origins_map,
        )
        rel_entries = _build_entries_for_bucket(
            rel_diff.get(om4ov_key, []),
            instance_counts,
            "relationship",
            source_origins_map,
        )
        merged = entity_entries + rel_entries
        # Re-sort merged + apply cap (entities and relationships were
        # individually capped; merging may exceed the cap so re-cap).
        merged.sort(key=lambda e: (-e.instance_count, e.element_name))
        buckets_payload[recon_bucket] = merged[:_PER_BUCKET_CAP]

    buckets = [
        DivergenceMapBucket(bucket_name=b, entries=buckets_payload[b])
        for b in _BUCKET_ORDER
    ]

    map_id = uuid4()
    generated_at = datetime.now(timezone.utc)
    reviewer_a = version_a.reviewer or ""
    reviewer_b = version_b.reviewer or ""

    response = DivergenceMapResponse(
        map_id=map_id,
        segment_id=segment_id,
        reviewer_a=reviewer_a,
        reviewer_b=reviewer_b,
        version_a_id=version_a_id,
        version_b_id=version_b_id,
        buckets=buckets,
        generated_at=generated_at,
    )

    # Persist to recon_divergence_maps.
    buckets_json = [b.model_dump(mode="json") for b in buckets]
    db_session.execute(
        text(
            """
            INSERT INTO recon_divergence_maps
                (id, segment_id, reviewer_a, reviewer_b,
                 version_a_id, version_b_id, buckets, generated_at)
            VALUES
                (:id, :seg, :ra, :rb,
                 :va, :vb, CAST(:bk AS JSONB), :gen)
            """
        ),
        {
            "id": str(map_id),
            "seg": segment_id,
            "ra": reviewer_a,
            "rb": reviewer_b,
            "va": str(version_a_id),
            "vb": str(version_b_id),
            "bk": json.dumps(buckets_json),
            "gen": generated_at,
        },
    )
    db_session.commit()

    logger.info(
        "recon.divergence_map.generated",
        map_id=str(map_id),
        segment_id=segment_id,
        reviewer_a=reviewer_a,
        reviewer_b=reviewer_b,
    )
    return response


def hydrate_divergence_map_response(row) -> DivergenceMapResponse:
    """Re-construct a ``DivergenceMapResponse`` from a SQL row of
    ``recon_divergence_maps``. The ``buckets`` JSONB column is returned
    as a list[dict] by psycopg2; pass through ``model_validate``.
    """
    payload = row.buckets
    if isinstance(payload, str):
        payload = json.loads(payload)
    buckets = [DivergenceMapBucket.model_validate(b) for b in payload]
    return DivergenceMapResponse(
        map_id=row.id,
        segment_id=row.segment_id,
        reviewer_a=row.reviewer_a,
        reviewer_b=row.reviewer_b,
        version_a_id=row.version_a_id,
        version_b_id=row.version_b_id,
        buckets=buckets,
        generated_at=row.generated_at,
    )
