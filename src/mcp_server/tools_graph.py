"""Graph tools: entity lookup, relationship lookup, health, info.

``grace_get_entity`` is the only two-mode tool in Chunk 26: it
accepts either a ``grace_id`` or a ``(type, name)`` pair, dispatches
to one of two allowlisted routes, and returns a Layer B
``SEMANTIC_INVALID_PARAMS`` envelope when the caller supplies both
or neither (spec §7.3).
"""

from __future__ import annotations

from src.mcp_server import http_client
from src.mcp_server.errors import MCPErrorEnvelope
from src.mcp_server.server import mcp, readonly_tool


@mcp.tool()
async def grace_get_entity(
    grace_id: str | None = None,
    type: str | None = None,
    name: str | None = None,
) -> dict:
    """Look up one entity in the graph either by `grace_id` (direct
    lookup) or by the `(type, name)` pair (canonical lookup).
    Provide exactly one of: `grace_id` alone, OR both `type` and
    `name`. Returns the entity record with its properties and
    metadata. Use when you know which entity you want and need the
    full record."""
    direct = grace_id is not None
    canonical = type is not None and name is not None
    if direct == canonical:
        return MCPErrorEnvelope(
            code="SEMANTIC_INVALID_PARAMS",
            message=(
                "Provide exactly one of: grace_id alone, OR both "
                "type and name."
            ),
            tool="grace_get_entity",
        ).model_dump()

    if direct:
        return await http_client.call(
            "GET",
            "/api/graph/entities/{grace_id}",
            tool="grace_get_entity",
            path_params={"grace_id": grace_id},
        )
    return await http_client.call(
        "GET",
        "/api/graph/entities/lookup",
        tool="grace_get_entity",
        query_params={"type": type, "name": name},
    )


# Two-mode tools record every route they may target so the
# endpoint-mapping contract test can walk both allowlist entries.
grace_get_entity.__grace_routes__ = frozenset(  # type: ignore[attr-defined]
    {
        ("GET", "/api/graph/entities/{grace_id}"),
        ("GET", "/api/graph/entities/lookup"),
    }
)


def _strip_embeddings(record: dict) -> dict:
    """Drop the 768-dim ``_embedding`` vector (and ArcadeDB-internal
    ``@rid``/``@cat``) from a vertex record so neighborhood payloads
    stay readable in the caller's context window. The embedding is
    machine-only; it is never useful to the LLM consuming the tool."""
    if not isinstance(record, dict):
        return record
    return {
        k: v
        for k, v in record.items()
        if k not in ("_embedding", "@rid", "@cat")
    }


@readonly_tool("GET", "/api/graph/entities/{grace_id}/neighborhood")
async def grace_get_neighborhood(grace_id: str, depth: int = 1) -> dict:
    """Return the 1- or 2-hop neighborhood of one entity: the entity
    itself (`seed`), its connected `neighbors`, and the `edges` joining
    them. `depth` must be 1 or 2 (2 is broader but capped at 50
    neighbors per hop). Use this when you need to know what a specific
    entity is *connected to* — `grace_get_entity` returns only a node's
    own properties, not its relationships, and `grace_search` surfaces
    only the top-ranked edges. This is the tool to confirm or deny that
    a given edge (e.g. an Agreement's governing Jurisdiction, or an
    entity's counterparties) exists in the graph."""
    result = await http_client.call(
        "GET",
        "/api/graph/entities/{grace_id}/neighborhood",
        tool="grace_get_neighborhood",
        path_params={"grace_id": grace_id},
        query_params={"depth": depth},
    )
    if isinstance(result, dict):
        if isinstance(result.get("seed"), dict):
            result["seed"] = _strip_embeddings(result["seed"])
        if isinstance(result.get("neighbors"), list):
            result["neighbors"] = [
                _strip_embeddings(n) for n in result["neighbors"]
            ]
    return result


@readonly_tool("GET", "/api/graph/relationships/{grace_id}")
async def grace_get_relationship(grace_id: str) -> dict:
    """Fetch one graph relationship (edge) by its `grace_id`. Returns
    the relationship record with its endpoints, predicate, and
    temporal validity. Use when an entity lookup or search result
    points to a relationship id you want to expand."""
    return await http_client.call(
        "GET",
        "/api/graph/relationships/{grace_id}",
        tool="grace_get_relationship",
        path_params={"grace_id": grace_id},
    )


@readonly_tool("GET", "/api/graph/health")
async def grace_graph_health() -> dict:
    """Check whether the graph database is reachable and responsive.
    Returns a small status payload. Use as a pre-flight when graph
    calls start failing, to distinguish "graph down" from "query
    returned nothing"."""
    return await http_client.call(
        "GET",
        "/api/graph/health",
        tool="grace_graph_health",
    )


@readonly_tool("GET", "/api/graph/info")
async def grace_graph_info() -> dict:
    """Report current graph server metadata — version, databases,
    and schema snapshot. Returns a nested dict. Use when you want
    high-level stats about what's in the graph right now."""
    return await http_client.call(
        "GET",
        "/api/graph/info",
        tool="grace_graph_info",
    )


@readonly_tool("GET", "/api/graph/counts")
async def grace_graph_counts() -> dict:
    """Return exact graph-wide counts: `total_entities`,
    `total_relationships`, and per-type maps `entity_counts` and
    `relationship_counts` (nonzero types, highest first). These are
    authoritative `count(*)` figures over the whole graph — use this,
    not `grace_search`, to answer "how many X are there" or "how many
    of each type" questions, since search results are top-k capped and
    will undercount. The entity types named for the active domain are the
    source content; the `*_Event`, `Document_Chunk`, and `Image_Asset`
    types are graph-internal bookkeeping, not domain data."""
    return await http_client.call(
        "GET",
        "/api/graph/counts",
        tool="grace_graph_counts",
    )


@readonly_tool("GET", "/api/graph/aggregate")
async def grace_graph_aggregate(edge_type: str, direction: str = "in") -> dict:
    """Return a ranked, graph-wide count of one relationship type grouped
    by the node at one end — the authoritative way to answer "which X has
    the most Y" without enumerating by hand. `edge_type` is a relationship
    type from the active schema (see `grace_graph_counts`'s
    `relationship_counts`). `direction="in"` groups by the edge's target
    (e.g. `edge_type="governed_by"` ranks the jurisdictions the most
    agreements point to); `direction="out"` groups by the edge's source
    (e.g. `edge_type="party_to"` ranks the entities party to the most
    agreements). Returns `{edge_type, direction, total_edges, counts}`
    with `counts` highest-first. An unknown `edge_type` returns the list
    of valid edge types. Domain-agnostic — works for any relationship in
    any ontology."""
    return await http_client.call(
        "GET",
        "/api/graph/aggregate",
        tool="grace_graph_aggregate",
        query_params={"edge_type": edge_type, "direction": direction},
    )


@readonly_tool("GET", "/api/graph/relationship-coverage")
async def grace_relationship_coverage() -> dict:
    """Return a whole-graph relationship-completeness report: for each
    domain relationship type, how many of its source entities actually
    carry the edge. Use this to answer "where is the graph thin / what
    did extraction miss" — e.g. it reveals that only 19 of 25 Agreements
    have a `governed_by` edge, or that an expected relationship is barely
    populated. Each row has `relationship`, `source_type`, `source_total`,
    `sources_with_edge`, `coverage_pct` (completeness, not confidence),
    and `edge_count`; rows are sorted thinnest-coverage-first so the
    biggest gaps are at the top. Domain-agnostic — derives expected
    source types from the graph, assumes no particular ontology."""
    return await http_client.call(
        "GET",
        "/api/graph/relationship-coverage",
        tool="grace_relationship_coverage",
    )
