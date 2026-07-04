import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { RetrievalInspector } from "@/components/inspector/RetrievalInspector";
import { useInspectorStore } from "@/lib/state/inspector-store";

const originalFetch = globalThis.fetch;

// Stub Next's useSearchParams to a controllable value.
let mockSearchParams = new URLSearchParams();
vi.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
}));

function withClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const RETRIEVAL_RESPONSE = {
  query: "hi",
  results: [
    {
      grace_id: "g-1",
      entity_type: "Legal_Entity",
      name: "Acme",
      properties: {},
      rerank_score: 0.9,
      rrf_score: 0.8,
      contributing_strategies: ["graph"],
      hop_distance: null,
    },
  ],
  serialized_context: "Entity: Acme",
  serialization_format: "template",
  total_candidates: 1,
  strategy_contributions: { graph: 1 },
  latency_ms: { graph: 100 },
  retrieval_mode: "single_round",
  query_intents: [],
  properties_omitted_count: 0,
  multi_hop_proxy_score: 0,
  latency_p95_by_mode_ms: {},
};

beforeEach(() => {
  useInspectorStore.getState().clearInspector();
  mockSearchParams = new URLSearchParams();
});

describe("RetrievalInspector", () => {
  it("renders empty state when no query has been run", () => {
    mockSearchParams = new URLSearchParams("source=direct_nav");
    withClient(<RetrievalInspector />);
    expect(screen.getByTestId("inspector-empty-state")).toBeTruthy();
    // direct_nav → replay caveat hidden
    expect(screen.queryByTestId("replay-caveat-banner")).toBeNull();
  });

  it("running a query from the inspector input populates all panels", async () => {
    mockSearchParams = new URLSearchParams("source=direct_nav");
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify(RETRIEVAL_RESPONSE), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    ) as unknown as typeof fetch;
    try {
      withClient(<RetrievalInspector />);
      const input = screen.getByTestId("inspector-query-input");
      fireEvent.change(input, { target: { value: "hi" } });
      fireEvent.submit(input.closest("form")!);

      await waitFor(() => {
        expect(screen.getByTestId("strategy-breakdown-chart")).toBeTruthy();
      });
      expect(screen.getByTestId("results-ranked-list")).toBeTruthy();
      expect(screen.getByTestId("serialized-context-viewer")).toBeTruthy();
      expect(screen.getByTestId("latency-breakdown")).toBeTruthy();
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
