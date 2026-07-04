"use client";

// D267 (Chunk 35b) — Query/Response audit mini-graph for the retrieval
// inspector. Fetches the subgraph via the backend `/api/retrieval/query-events/{id}/subgraph`
// endpoint and renders nodes/edges through the shared GraphCanvas wrapper.
//
// D217 discipline (NB2 clarification): numeric scores (`rrf_score`,
// `rerank_score`, `rank_ordinal`) MUST NOT reach the DOM as visible text,
// `data-*` numeric attributes, or tooltip content. `rank_ordinal` may
// pass through the API JSON for layout ordering but is not rendered.

import { useEffect, useState } from "react";
import {
  GraphCanvas,
  type GraphEdgeData,
  type GraphNodeData,
} from "@/components/graph/GraphCanvas";

export type QueryAuditSubgraphNode = {
  data: {
    id: string;
    label: string;
    type: string;
    group: "query_event" | "entity";
  };
};

export type QueryAuditSubgraphEdge = {
  data: {
    id: string;
    source: string;
    target: string;
    type: string;
    rank_ordinal?: number | null;
  };
};

export type QueryAuditSubgraph = {
  query_event_id: string;
  nodes: QueryAuditSubgraphNode[];
  edges: QueryAuditSubgraphEdge[];
};

export type QueryAuditGraphProps = {
  /** When passed, the component renders the supplied subgraph directly
   * (no fetch). Used by tests and by parent containers that have already
   * fetched the data. */
  subgraph?: QueryAuditSubgraph | null;
  /** When `subgraph` is omitted, the component fetches this id from the
   * inspector subgraph route. */
  queryEventId?: string | null;
  /** Optional fetch override (for testing). */
  fetchImpl?: typeof fetch;
};

function projectNode(n: QueryAuditSubgraphNode): GraphNodeData {
  return {
    id: n.data.id,
    label: n.data.label,
    entityType: n.data.type,
    // Use `group` as the ontology-module proxy so query_event renders
    // distinctly from domain entities. No score data is mapped here.
    ontologyModule: n.data.group,
  };
}

function projectEdge(e: QueryAuditSubgraphEdge): GraphEdgeData {
  // D217: edge label is the relationship type (`retrieved_from`) — never
  // a numeric score or rank. `rank_ordinal` is intentionally dropped from
  // GraphEdgeData; it stays in the API JSON for layout ordering only.
  return {
    id: e.data.id,
    source: e.data.source,
    target: e.data.target,
    label: e.data.type,
  };
}

export function QueryAuditGraph(props: QueryAuditGraphProps) {
  const { subgraph: provided, queryEventId, fetchImpl } = props;
  const [fetched, setFetched] = useState<QueryAuditSubgraph | null>(
    provided ?? null,
  );
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (provided !== undefined) return;
    if (!queryEventId) return;
    let cancelled = false;
    const f = fetchImpl ?? globalThis.fetch;
    f(`/api/retrieval/query-events/${queryEventId}/subgraph`, {
      headers: { "X-Graph-Scope": "all" },
    })
      .then(async (resp) => {
        if (!resp.ok) {
          throw new Error(`subgraph fetch failed: ${resp.status}`);
        }
        const json = (await resp.json()) as QueryAuditSubgraph;
        if (!cancelled) setFetched(json);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [provided, queryEventId, fetchImpl]);

  const subgraph = provided ?? fetched;

  if (error) {
    return (
      <div
        data-testid="query-audit-graph-error"
        className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md p-3"
      >
        Failed to load query audit subgraph.
      </div>
    );
  }

  if (!subgraph || (subgraph.nodes.length === 0 && subgraph.edges.length === 0)) {
    return (
      <div
        data-testid="query-audit-graph-empty"
        className="text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-md p-3"
      >
        No query audit data available.
      </div>
    );
  }

  const nodes: GraphNodeData[] = subgraph.nodes.map(projectNode);
  const edges: GraphEdgeData[] = subgraph.edges.map(projectEdge);

  return (
    <div data-testid="query-audit-graph" className="w-full h-full">
      <GraphCanvas nodes={nodes} edges={edges} layout="fcose" />
    </div>
  );
}
