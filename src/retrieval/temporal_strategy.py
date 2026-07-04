"""Strategy 4 / Filter: Temporal date-range scoring and filtering."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string
from src.retrieval.retrieval_config import RetrievalConfig
from src.retrieval.retrieval_models import RetrievalCandidate, RetrievalQuery

logger = structlog.get_logger()


class TemporalMode(str, Enum):
    """How temporal filtering is applied."""

    FILTER = "filter"  # Applied as WHERE clause on graph traversal
    STRATEGY = "strategy"  # Runs as separate RRF input


async def temporal_search(
    client: ArcadeClient,
    query: RetrievalQuery,
    config: RetrievalConfig,
) -> list[RetrievalCandidate]:
    """Temporal-only retrieval: find entities by date range relevance.

    Only called when config.temporal_as_strategy=True.
    Queries graph for entities whose valid_from/valid_to overlap the query window.
    Ranks by temporal proximity.
    """
    where_parts = ["m._deprecated = false"]

    if query.temporal_start:
        ts = escape_cypher_string(query.temporal_start.isoformat())
        where_parts.append(f"(m.valid_from IS NULL OR m.valid_from <= '{ts}')")
    if query.temporal_end:
        te = escape_cypher_string(query.temporal_end.isoformat())
        where_parts.append(f"(m.valid_to IS NULL OR m.valid_to >= '{te}')")

    if query.entity_types:
        type_list = ", ".join(
            f"'{escape_cypher_string(t)}'" for t in query.entity_types
        )
        where_parts.append(f"m.`@type` IN [{type_list}]")

    where_clause = " AND ".join(where_parts)
    cypher = (
        f"MATCH (m) WHERE {where_clause} "
        f"RETURN m LIMIT {config.temporal_result_limit}"
    )

    result = await client.execute_cypher(cypher)
    rows = result.get("result", [])

    candidates: list[RetrievalCandidate] = []
    for rank, row in enumerate(rows):
        entity = row.get("m", row) if isinstance(row.get("m"), dict) else row
        gid = entity.get("grace_id", "")
        if not gid:
            continue
        candidates.append(
            RetrievalCandidate(
                grace_id=gid,
                entity_type=entity.get("@type", "Entity"),
                name=entity.get("name", ""),
                properties={
                    k: v for k, v in entity.items()
                    if k not in ("@rid", "@type", "@cat", "@in", "@out")
                },
                score=1.0,
                strategy="temporal",
                rank=rank + 1,
            )
        )

    return candidates


def apply_temporal_filter(
    candidates: list[RetrievalCandidate],
    temporal_start: datetime | None,
    temporal_end: datetime | None,
) -> list[RetrievalCandidate]:
    """Post-retrieval filter: remove candidates outside temporal window.

    Applied when config.temporal_as_strategy=False (default).
    Entities with null valid_from/valid_to are kept (assumed current).
    """
    if temporal_start is None and temporal_end is None:
        return candidates

    filtered: list[RetrievalCandidate] = []
    for c in candidates:
        valid_from = c.properties.get("valid_from")
        valid_to = c.properties.get("valid_to")

        # Parse string dates if needed
        vf = _parse_dt(valid_from)
        vt = _parse_dt(valid_to)

        # Keep if temporal bounds overlap or are null
        if temporal_end and vf is not None and vf > temporal_end:
            continue
        if temporal_start and vt is not None and vt < temporal_start:
            continue

        filtered.append(c)

    return filtered


def _parse_dt(value: str | datetime | None) -> datetime | None:
    """Parse a datetime value from string or return as-is.

    Always returns timezone-aware datetime (UTC) for consistent comparison.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None
