"""Meta tool: ``grace_explain_capabilities``.

This is the only tool in Chunk 26 without an HTTP leg. It uses bare
``@mcp.tool()`` (no ``@readonly_tool``) because it targets no
upstream route.
"""

from __future__ import annotations

from src.mcp_server.server import mcp


_CAPABILITIES_MD = """# GrACE MCP capabilities

GrACE is a knowledge graph with an ontology that evolves under
human review. Over MCP you can:

- **Search the graph** (`grace_search`) for ranked entities and
  relationships that match a natural-language query.
- **Get a grounded answer** (`grace_answer`) that synthesises text
  from retrieved context and attaches per-span provenance.
- **Inspect the ontology** via `grace_get_active_schema`,
  `grace_get_module_schema`, and `grace_list_schema_versions`.
- **Look up entities and relationships** by id or canonical name via
  `grace_get_entity` and `grace_get_relationship`.
- **Review the competency-question catalog** via `grace_list_cqs`
  and `grace_cq_summary`.
- **Check system health** via `grace_graph_health`,
  `grace_graph_info`, and `grace_ollama_health`.

Answers from `grace_answer` include claim spans with supporting
graph ids — use those to cite the graph when presenting results to
the user.
"""


@mcp.tool()
async def grace_explain_capabilities() -> str:
    """Return a static capabilities summary describing what GrACE
    exposes over MCP: graph search, grounded answers with
    citations, ontology inspection, and the competency-question
    catalog. Call this first when deciding which other GrACE tool
    to use. Returns markdown prose."""
    return _CAPABILITIES_MD
