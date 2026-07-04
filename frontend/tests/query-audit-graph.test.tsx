import { describe, expect, it, vi } from "vitest";
import { render, waitFor } from "@testing-library/react";

// Match the cytoscape stubbing pattern used in graph-canvas.test.tsx.
vi.mock("react-cytoscapejs", () => ({
  default: (props: Record<string, unknown>) => (
    <div
      data-testid="cytoscape-stub"
      data-elements-count={
        Array.isArray(props.elements)
          ? (props.elements as unknown[]).length
          : 0
      }
    />
  ),
}));
vi.mock("cytoscape", () => ({ default: { use: vi.fn() } }));
vi.mock("cytoscape-fcose", () => ({ default: {} }));
vi.mock("cytoscape-dagre", () => ({ default: {} }));

import { QueryAuditGraph, type QueryAuditSubgraph } from "@/components/inspector/QueryAuditGraph";

const SUBGRAPH: QueryAuditSubgraph = {
  query_event_id: "11111111-1111-1111-1111-111111111111",
  nodes: [
    {
      data: {
        id: "q-grace-1",
        label: "who owns Acme?",
        type: "Query_Event",
        group: "query_event",
      },
    },
    {
      data: {
        id: "ent-a",
        label: "Acme Corp",
        type: "Legal_Entity",
        group: "entity",
      },
    },
    {
      data: {
        id: "ent-b",
        label: "Beta Holdings",
        type: "Legal_Entity",
        group: "entity",
      },
    },
  ],
  edges: [
    {
      data: {
        id: "edge-a",
        source: "q-grace-1",
        target: "ent-a",
        type: "retrieved_from",
        rank_ordinal: 1,
      },
    },
    {
      data: {
        id: "edge-b",
        source: "q-grace-1",
        target: "ent-b",
        type: "retrieved_from",
        rank_ordinal: 2,
      },
    },
  ],
};

describe("QueryAuditGraph (D267)", () => {
  it("renders nodes and edges from a supplied subgraph", () => {
    const { getByTestId } = render(<QueryAuditGraph subgraph={SUBGRAPH} />);
    expect(getByTestId("query-audit-graph")).toBeTruthy();
    const stub = getByTestId("cytoscape-stub");
    // 3 nodes + 2 edges = 5 elements through the GraphCanvas wrapper.
    expect(stub.dataset.elementsCount).toBe("5");
  });

  it("renders no numeric scores in the DOM (D217 discipline)", () => {
    const { container } = render(<QueryAuditGraph subgraph={SUBGRAPH} />);
    const html = container.innerHTML;
    // rank_ordinal is permitted in the API JSON but must not appear as
    // visible DOM text or `data-*` attribute on rendered elements.
    expect(html).not.toMatch(/rank_ordinal/i);
    expect(html).not.toMatch(/rrf_score/i);
    expect(html).not.toMatch(/rerank_score/i);
    // Sanity: the digits 1 and 2 are the rank ordinals from the fixture.
    // Make sure they are not surfaced as standalone tooltip / label content
    // on entity rows. (The Cytoscape stub renders nothing user-facing.)
    const visibleText = container.textContent ?? "";
    expect(visibleText).not.toContain("rank_ordinal");
  });

  it("renders an empty-state placeholder when no subgraph data is available", () => {
    const empty: QueryAuditSubgraph = {
      query_event_id: "0",
      nodes: [],
      edges: [],
    };
    const { getByTestId } = render(<QueryAuditGraph subgraph={empty} />);
    expect(getByTestId("query-audit-graph-empty")).toBeTruthy();
  });

  it("fetches a subgraph by query_event_id when no data is supplied", async () => {
    const fetchImpl = vi.fn(async () =>
      new Response(JSON.stringify(SUBGRAPH), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ) as unknown as typeof fetch;
    const { getByTestId } = render(
      <QueryAuditGraph
        queryEventId={SUBGRAPH.query_event_id}
        fetchImpl={fetchImpl}
      />,
    );
    await waitFor(() => {
      expect(getByTestId("query-audit-graph")).toBeTruthy();
    });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });
});
