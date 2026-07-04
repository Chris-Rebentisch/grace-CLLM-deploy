"""Graph neighborhood queries for entity context retrieval.

Used by MINE sampler to build graph context for fact-checking.
"""

from __future__ import annotations

import structlog

from src.graph.arcade_client import ArcadeClient
from src.graph.cypher_utils import escape_cypher_string

logger = structlog.get_logger()


async def fetch_entity_neighborhood(
    client: ArcadeClient,
    grace_id: str,
    max_depth: int = 2,
) -> dict:
    """Fetch entity + connected entities + edges within max_depth hops.

    Uses BIDIRECTIONAL traversal — both outgoing and incoming edges —
    so edges pointing INTO the seed entity are included.

    Implementation uses a UNION of directed out + directed in queries.
    ArcadeDB's native OpenCypher engine does not reliably support
    undirected variable-length patterns, so the UNION fallback is used.

    Returns:
        {
            "seed": dict (entity properties),
            "neighbors": list[dict] (connected entity properties),
            "edges": list[dict] (edge properties with source/target grace_ids)
        }
    """
    escaped_gid = escape_cypher_string(grace_id)
    result: dict = {"seed": {}, "neighbors": [], "edges": []}

    # Fetch seed entity (exclude deprecated — same convention as graph_strategy)
    seed_query = (
        f"MATCH (seed {{grace_id: '{escaped_gid}'}}) "
        f"WHERE seed._deprecated = false "
        f"RETURN seed LIMIT 1"
    )
    try:
        seed_result = await client.execute_cypher(seed_query)
        seed_rows = seed_result.get("result", [])
        if not seed_rows:
            logger.warning("neighborhood.seed_not_found", grace_id=grace_id)
            return result
        seed_row = seed_rows[0]
        result["seed"] = seed_row.get("seed", seed_row) if isinstance(seed_row, dict) else seed_row
    except Exception:
        logger.warning("neighborhood.seed_query_failed", grace_id=grace_id, exc_info=True)
        return result

    # Fetch outgoing neighbors and edges
    out_query = (
        f"MATCH (seed {{grace_id: '{escaped_gid}'}})-[r]->(m) "
        f"WHERE m._deprecated = false "
        f"RETURN seed.grace_id AS source_grace_id, "
        f"type(r) AS relationship_type, "
        f"m.grace_id AS target_grace_id, "
        f"m AS neighbor, r AS edge "
        f"LIMIT 100"
    )

    # Fetch incoming neighbors and edges
    in_query = (
        f"MATCH (seed {{grace_id: '{escaped_gid}'}})<-[r]-(m) "
        f"WHERE m._deprecated = false "
        f"RETURN m.grace_id AS source_grace_id, "
        f"type(r) AS relationship_type, "
        f"seed.grace_id AS target_grace_id, "
        f"m AS neighbor, r AS edge "
        f"LIMIT 100"
    )

    seen_neighbor_ids: set[str] = set()
    seen_edge_keys: set[tuple] = set()

    for query in [out_query, in_query]:
        try:
            query_result = await client.execute_cypher(query)
            rows = query_result.get("result", [])
            for row in rows:
                # Extract neighbor
                neighbor = row.get("neighbor", {})
                if isinstance(neighbor, dict):
                    ngid = neighbor.get("grace_id", "")
                    if ngid and ngid not in seen_neighbor_ids:
                        seen_neighbor_ids.add(ngid)
                        result["neighbors"].append(neighbor)

                # Extract edge
                source_gid = row.get("source_grace_id", "")
                target_gid = row.get("target_grace_id", "")
                rel_type = row.get("relationship_type", "related_to")
                edge_key = (source_gid, target_gid, rel_type)
                if edge_key not in seen_edge_keys:
                    seen_edge_keys.add(edge_key)
                    edge_data = row.get("edge", {})
                    if not isinstance(edge_data, dict):
                        edge_data = {}
                    edge_entry = {
                        **edge_data,
                        "source_grace_id": source_gid,
                        "target_grace_id": target_gid,
                        "relationship_type": rel_type,
                    }
                    result["edges"].append(edge_entry)
        except Exception:
            logger.warning(
                "neighborhood.query_failed",
                grace_id=grace_id,
                exc_info=True,
            )

    # For max_depth > 1, fetch 2-hop neighbors from discovered neighbors
    if max_depth >= 2 and seen_neighbor_ids:
        await _fetch_depth2(
            client, seen_neighbor_ids, grace_id, result, seen_neighbor_ids, seen_edge_keys
        )

    logger.info(
        "neighborhood.fetched",
        grace_id=grace_id,
        neighbor_count=len(result["neighbors"]),
        edge_count=len(result["edges"]),
        max_depth=max_depth,
    )
    return result


async def _fetch_depth2(
    client: ArcadeClient,
    depth1_ids: set[str],
    seed_id: str,
    result: dict,
    seen_neighbor_ids: set[str],
    seen_edge_keys: set[tuple],
) -> None:
    """Extend neighborhood to depth 2 by querying from depth-1 neighbors."""
    for neighbor_gid in list(depth1_ids):
        escaped = escape_cypher_string(neighbor_gid)
        for direction_query in [
            (
                f"MATCH (n {{grace_id: '{escaped}'}})-[r]->(m) "
                f"WHERE m._deprecated = false "
                f"RETURN n.grace_id AS source_grace_id, "
                f"type(r) AS relationship_type, "
                f"m.grace_id AS target_grace_id, "
                f"m AS neighbor, r AS edge "
                f"LIMIT 50"
            ),
            (
                f"MATCH (n {{grace_id: '{escaped}'}})<-[r]-(m) "
                f"WHERE m._deprecated = false "
                f"RETURN m.grace_id AS source_grace_id, "
                f"type(r) AS relationship_type, "
                f"n.grace_id AS target_grace_id, "
                f"m AS neighbor, r AS edge "
                f"LIMIT 50"
            ),
        ]:
            try:
                query_result = await client.execute_cypher(direction_query)
                rows = query_result.get("result", [])
                for row in rows:
                    neighbor = row.get("neighbor", {})
                    if isinstance(neighbor, dict):
                        ngid = neighbor.get("grace_id", "")
                        if ngid and ngid not in seen_neighbor_ids and ngid != seed_id:
                            seen_neighbor_ids.add(ngid)
                            result["neighbors"].append(neighbor)

                    source_gid = row.get("source_grace_id", "")
                    target_gid = row.get("target_grace_id", "")
                    rel_type = row.get("relationship_type", "related_to")
                    edge_key = (source_gid, target_gid, rel_type)
                    if edge_key not in seen_edge_keys:
                        seen_edge_keys.add(edge_key)
                        edge_data = row.get("edge", {})
                        if not isinstance(edge_data, dict):
                            edge_data = {}
                        edge_entry = {
                            **edge_data,
                            "source_grace_id": source_gid,
                            "target_grace_id": target_gid,
                            "relationship_type": rel_type,
                        }
                        result["edges"].append(edge_entry)
            except Exception:
                logger.warning(
                    "neighborhood.depth2_query_failed",
                    neighbor_gid=neighbor_gid,
                    exc_info=True,
                )
