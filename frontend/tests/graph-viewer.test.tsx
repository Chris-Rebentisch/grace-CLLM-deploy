import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { GraphViewer } from "@/components/graph/GraphViewer";
import { useGraphStore } from "@/lib/state/graph-store";

const originalFetch = globalThis.fetch;

// Same Cytoscape stub pattern as graph-canvas.test.tsx — jsdom has no Canvas.
vi.mock("react-cytoscapejs", () => ({
  default: (props: Record<string, unknown>) => {
    return (
      <div
        data-testid="cytoscape-stub"
        data-elements-count={
          Array.isArray(props.elements)
            ? (props.elements as unknown[]).length
            : 0
        }
      />
    );
  },
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

function routedFetch(responders: Record<string, unknown>) {
  return async (url: string) => {
    const u = new URL(url);
    for (const path of Object.keys(responders)) {
      if (u.pathname === path) {
        return new Response(JSON.stringify(responders[path]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
}

beforeEach(() => {
  useGraphStore.getState().reset();
});

const SAMPLE_ENTITIES = {
  entities: [
    {
      grace_id: "e-1",
      entity_type: "Legal_Entity",
      properties: { name: "Acme" },
      source_document_id: "d-1",
      extraction_event_id: "ev-1",
      ontology_module: "legal_entity",
      human_validated: true,
      valid_from: null,
      valid_to: null,
      extraction_confidence: null,
    },
    {
      grace_id: "e-2",
      entity_type: "Contract",
      properties: { name: "Master Agreement" },
      source_document_id: null,
      extraction_event_id: null,
      ontology_module: "contract",
      human_validated: false,
      valid_from: null,
      valid_to: null,
      extraction_confidence: null,
    },
  ],
  next_cursor: null,
};

const SAMPLE_RELATIONSHIPS = {
  relationships: [
    {
      grace_id: "r-1",
      relationship_type: "signed",
      source_grace_id: "e-1",
      target_grace_id: "e-2",
      properties: {},
      source_document_id: null,
      extraction_event_id: null,
      ontology_module: null,
      human_validated: false,
      extraction_confidence: null,
    },
  ],
  next_cursor: null,
};

describe("GraphViewer", () => {
  it("renders a populated graph with toolbar and legend", async () => {
    globalThis.fetch = vi.fn(
      routedFetch({
        "/api/graph/entities": SAMPLE_ENTITIES,
        "/api/graph/relationships": SAMPLE_RELATIONSHIPS,
      }),
    ) as unknown as typeof fetch;
    try {
      withClient(<GraphViewer />);
      await waitFor(() => {
        expect(screen.getByTestId("graph-canvas-wrap")).toBeTruthy();
      });
      expect(screen.getByTestId("graph-toolbar")).toBeTruthy();
      expect(screen.getByTestId("type-filter-legend")).toBeTruthy();
      // 2 entities + 1 edge → 3 Cytoscape elements
      expect(
        screen.getByTestId("cytoscape-stub").dataset.elementsCount,
      ).toBe("3");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("renders the empty state when entities list is empty", async () => {
    globalThis.fetch = vi.fn(
      routedFetch({
        "/api/graph/entities": { entities: [], next_cursor: null },
        "/api/graph/relationships": { relationships: [], next_cursor: null },
      }),
    ) as unknown as typeof fetch;
    try {
      withClient(<GraphViewer />);
      await waitFor(() => {
        expect(screen.getByTestId("graph-empty-state")).toBeTruthy();
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("layout toggle flips store from fcose to dagre", async () => {
    globalThis.fetch = vi.fn(
      routedFetch({
        "/api/graph/entities": SAMPLE_ENTITIES,
        "/api/graph/relationships": SAMPLE_RELATIONSHIPS,
      }),
    ) as unknown as typeof fetch;
    try {
      withClient(<GraphViewer />);
      await waitFor(() => {
        expect(screen.getByTestId("graph-canvas-wrap")).toBeTruthy();
      });
      expect(useGraphStore.getState().activeLayout).toBe("fcose");
      fireEvent.click(screen.getByTestId("layout-dagre"));
      expect(useGraphStore.getState().activeLayout).toBe("dagre");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
