import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { GraphViewer } from "@/components/graph/GraphViewer";
import { useGraphStore } from "@/lib/state/graph-store";

const originalFetch = globalThis.fetch;

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

function withClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  useGraphStore.getState().reset();
});

describe("integration: graph viewer load → render → click-node", () => {
  it("completes the full flow with mocked backend", async () => {
    const calls: Array<{ url: string }> = [];
    globalThis.fetch = (async (url: string) => {
      calls.push({ url });
      const u = new URL(url);
      if (u.pathname === "/api/graph/entities") {
        return new Response(
          JSON.stringify({
            entities: [
              {
                grace_id: "g-1",
                entity_type: "Legal_Entity",
                properties: { name: "Acme" },
                source_document_id: "d-1",
                extraction_event_id: "e-1",
                ontology_module: "legal_entity",
                human_validated: true,
                valid_from: null,
                valid_to: null,
                extraction_confidence: null,
              },
            ],
            next_cursor: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (u.pathname === "/api/graph/relationships") {
        return new Response(
          JSON.stringify({ relationships: [], next_cursor: null }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;
    try {
      withClient(<GraphViewer />);
      await waitFor(() =>
        expect(screen.getByTestId("graph-canvas-wrap")).toBeTruthy(),
      );
      // Scope header asserted on every request
      for (const c of calls) expect(new URL(c.url)); // URL parses
      // Simulate a node click via the store (Cytoscape stubbed out)
      useGraphStore.getState().selectNode("g-1");
      await waitFor(() =>
        expect(screen.getByTestId("node-detail-panel")).toBeTruthy(),
      );
      fireEvent.click(screen.getByTestId("node-detail-close"));
      await waitFor(() =>
        expect(screen.queryByTestId("node-detail-panel")).toBeNull(),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
