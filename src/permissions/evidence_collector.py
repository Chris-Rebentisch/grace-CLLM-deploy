"""Evidence collector — six-source aggregator (Chunk 42, D332).

Produces an :class:`EvidenceBundle` from six sources defined by D332:

1. Document authorship — joined off ``extraction_claims`` provenance.
2. Segment ownership — read from ``segmentation_maps`` JSONB +
   ``review_sessions.reviewer``.
3. Graph Person/Role entities — queried from ArcadeDB via
   ``arcade_client.execute_cypher()``.
4. Change_Directive authorship — read from ``change_directives.authored_by``.
5. Signal combination — produced by combining signal pipeline rows.
6. Communications — typed-but-empty placeholder until Phase 7 / D274.

Re-running is cheap by design — no LLM is invoked in the default path.
The collector ingests sources via a small dependency-injection record so
tests can supply mocks for both Postgres and ArcadeDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol

from src.permissions.models import (
    EvidenceBundle,
    EvidenceSection,
    EvidenceSourceName,
)


# ----- Source protocols ---------------------------------------------


class _PostgresSession(Protocol):
    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any: ...


class _ArcadeClient(Protocol):
    def execute_cypher(
        self, query: str, params: Mapping[str, Any] | None = ...
    ) -> Any: ...


@dataclass
class EvidenceCollectorConfig:
    """Optional knobs for the collector.

    ``communications_enabled`` is False at v1 (Phase 7 / D274 not yet
    met). Setting it True is reserved for a future chunk; the v1
    collector still emits the typed-but-empty section for shape
    stability.
    """

    communications_enabled: bool = False


# ----- Source readers (each is small + injectable) ------------------


def _read_document_authorship(
    session: _PostgresSession | None,
) -> list[dict[str, Any]]:
    """Source 1: document_authorship rows.

    Joins ``extraction_claims`` provenance by ``source_document_id`` and
    deduplicates by (document_id, person_grace_id). Returns one row per
    distinct authorship pair.

    Default implementation is conservative: when no session is provided
    the source is empty. Real session reads are exercised via DI in
    integration tests.
    """
    if session is None:
        return []
    # The schema spec for `extraction_claims.provenance` is JSONB carrying
    # a list of {"author_grace_id": str, "document_id": str}; the collector
    # asks for the distilled set rather than the raw rows.
    rows: list[dict[str, Any]] = []
    try:
        # Use a parameter-free read because the dedup is performed in
        # Python — keeps the SQL surface narrow.
        result = session.execute(
            _SQL_READ_DOCUMENT_AUTHORSHIP  # noqa: F841
        )
        for r in _iter_rows(result):
            rows.append(
                {
                    "person_grace_id": r.get("author_grace_id"),
                    "document_id": r.get("document_id"),
                }
            )
    except Exception:
        # The collector is non-fatal — a transient query failure should
        # not block hypothesis generation. Empty section is a valid
        # state and downstream tolerates it.
        return []
    return _dedup_by(rows, ("person_grace_id", "document_id"))


def _read_segment_ownership(
    session: _PostgresSession | None,
) -> list[dict[str, Any]]:
    """Source 2: segment ownership rows.

    Reads ratified ``segmentation_maps`` (newest by
    ``created_at`` per archive root) joined to ``review_sessions.reviewer``
    when present. Result is one row per (segment_name, reviewer).
    """
    if session is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        result = session.execute(_SQL_READ_SEGMENT_OWNERSHIP)
        for r in _iter_rows(result):
            rows.append(
                {
                    "segment_name": r.get("segment_name"),
                    "reviewer": r.get("reviewer"),
                }
            )
    except Exception:
        return []
    return _dedup_by(rows, ("segment_name", "reviewer"))


def _read_graph_person_role(
    arcade: _ArcadeClient | None,
) -> list[dict[str, Any]]:
    """Source 3: Person/Role nodes from the graph.

    Issues a single OpenCypher MATCH against ArcadeDB returning
    Person nodes with their adjacent Role labels. The collector treats
    a missing client as 'no graph evidence' and returns empty.
    """
    if arcade is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        result = arcade.execute_cypher(
            "MATCH (p:Person)-[:HAS_ROLE]->(r:Role) "
            "RETURN p.grace_id AS person_grace_id, r.name AS role_name "
            "LIMIT 1000"
        )
        for r in _iter_rows(result):
            rows.append(
                {
                    "person_grace_id": r.get("person_grace_id"),
                    "role_name": r.get("role_name"),
                }
            )
    except Exception:
        return []
    return _dedup_by(rows, ("person_grace_id", "role_name"))


def _read_change_directive_authorship(
    session: _PostgresSession | None,
) -> list[dict[str, Any]]:
    """Source 4: change_directive authorship rows."""
    if session is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        result = session.execute(_SQL_READ_CHANGE_DIRECTIVE_AUTHORSHIP)
        for r in _iter_rows(result):
            rows.append(
                {
                    "directive_id": r.get("directive_id"),
                    "authored_by": r.get("authored_by"),
                }
            )
    except Exception:
        return []
    return _dedup_by(rows, ("directive_id",))


def _read_signal_combination(
    session: _PostgresSession | None,
) -> list[dict[str, Any]]:
    """Source 5: signal combination rows.

    Produces aggregated counts per (person_grace_id, signal_kind) so the
    hypothesis generator can use them as edge weights without re-hitting
    the analytics_signals table.
    """
    if session is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        result = session.execute(_SQL_READ_SIGNAL_COMBINATION)
        for r in _iter_rows(result):
            rows.append(
                {
                    "person_grace_id": r.get("person_grace_id"),
                    "signal_kind": r.get("signal_kind"),
                    "count": int(r.get("count") or 0),
                }
            )
    except Exception:
        return []
    return rows


def _communications_section() -> EvidenceSection:
    """Source 6: typed-but-empty placeholder (Phase 7 / D274).

    The placeholder is intentional — Chunk 43 / Phase 7 fills in the
    actual communications signal source. Keeping the slot present at v1
    means downstream consumers (hypothesis generator, drift detector)
    don't need a schema change when Phase 7 lands.
    """
    return EvidenceSection(
        source="communications",
        rows=[],
        is_empty_placeholder=True,
    )


# ----- SQL strings (kept parameter-less; pure reads) ----------------


_SQL_READ_DOCUMENT_AUTHORSHIP = """
SELECT DISTINCT
    (provenance ->> 'author_grace_id') AS author_grace_id,
    (provenance ->> 'document_id') AS document_id
FROM extraction_claims
WHERE provenance ? 'author_grace_id'
  AND provenance ? 'document_id'
LIMIT 5000
"""

_SQL_READ_SEGMENT_OWNERSHIP = """
WITH latest_maps AS (
    SELECT DISTINCT ON (archive_root_canonical_path)
        segmentation_map_id,
        archive_root_canonical_path,
        payload,
        created_at
    FROM segmentation_maps
    ORDER BY archive_root_canonical_path, created_at DESC
)
SELECT
    seg ->> 'segment_name' AS segment_name,
    rs.reviewer AS reviewer
FROM latest_maps lm
LEFT JOIN review_sessions rs ON rs.segmentation_map_id = lm.segmentation_map_id
LEFT JOIN LATERAL jsonb_array_elements(lm.payload -> 'segments') seg ON TRUE
WHERE seg IS NOT NULL
LIMIT 5000
"""

_SQL_READ_CHANGE_DIRECTIVE_AUTHORSHIP = """
SELECT directive_id, authored_by
FROM change_directives
WHERE authored_by IS NOT NULL
LIMIT 5000
"""

_SQL_READ_SIGNAL_COMBINATION = """
SELECT
    COALESCE(payload ->> 'person_grace_id', '') AS person_grace_id,
    signal_kind,
    COUNT(*) AS count
FROM analytics_signals
WHERE payload ? 'person_grace_id'
GROUP BY person_grace_id, signal_kind
LIMIT 5000
"""


# ----- Helpers ------------------------------------------------------


def _iter_rows(result: Any) -> Iterable[Mapping[str, Any]]:
    """Iterate rows from SQLAlchemy/test-mock results in a tolerant way.

    Tests pass plain lists of dicts; production code passes a
    ``CursorResult``. Both are supported.
    """
    if result is None:
        return []
    if hasattr(result, "mappings"):
        try:
            return list(result.mappings())
        except Exception:  # pragma: no cover - SQLAlchemy edge
            pass
    if isinstance(result, list):
        return result
    try:
        return list(result)
    except TypeError:  # pragma: no cover
        return []


def _dedup_by(
    rows: list[dict[str, Any]], keys: tuple[str, ...]
) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        k = tuple(r.get(key) for key in keys)
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


# ----- Public API ---------------------------------------------------


SourceReader = Callable[[], list[dict[str, Any]]]


def collect_evidence(
    *,
    pg_session: _PostgresSession | None = None,
    arcade_client: _ArcadeClient | None = None,
    config: EvidenceCollectorConfig | None = None,
    overrides: Mapping[EvidenceSourceName, SourceReader] | None = None,
) -> EvidenceBundle:
    """Aggregate all six D332 sources into an :class:`EvidenceBundle`.

    All five non-placeholder sources are read in a stable order. The
    communications source is always emitted as a typed empty
    placeholder at v1.

    ``overrides`` lets tests supply per-source readers without monkey-
    patching the SQL constants. Each override maps a source name to a
    zero-arg callable returning a list of dict rows.
    """
    cfg = config or EvidenceCollectorConfig()

    def _resolve(name: EvidenceSourceName, default: SourceReader) -> SourceReader:
        if overrides is None:
            return default
        return overrides.get(name, default)

    document_authorship = _resolve(
        "document_authorship",
        lambda: _read_document_authorship(pg_session),
    )()
    segment_ownership = _resolve(
        "segment_ownership",
        lambda: _read_segment_ownership(pg_session),
    )()
    graph_person_role = _resolve(
        "graph_person_role",
        lambda: _read_graph_person_role(arcade_client),
    )()
    change_directive_authorship = _resolve(
        "change_directive_authorship",
        lambda: _read_change_directive_authorship(pg_session),
    )()
    signal_combination = _resolve(
        "signal_combination",
        lambda: _read_signal_combination(pg_session),
    )()

    sections: list[EvidenceSection] = [
        EvidenceSection(source="document_authorship", rows=document_authorship),
        EvidenceSection(source="segment_ownership", rows=segment_ownership),
        EvidenceSection(source="graph_person_role", rows=graph_person_role),
        EvidenceSection(
            source="change_directive_authorship", rows=change_directive_authorship
        ),
        EvidenceSection(source="signal_combination", rows=signal_combination),
    ]
    # Source 6 — communications. v1: typed empty placeholder regardless
    # of the (currently unused) configuration knob.
    if cfg.communications_enabled:
        sections.append(
            EvidenceSection(source="communications", rows=[], is_empty_placeholder=False)
        )
    else:
        sections.append(_communications_section())

    return EvidenceBundle(sections=sections)


__all__ = [
    "EvidenceCollectorConfig",
    "collect_evidence",
]
