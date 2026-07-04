import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { RetrievalInspector } from "@/components/inspector/RetrievalInspector";
import { useInspectorStore } from "@/lib/state/inspector-store";

const originalFetch = globalThis.fetch;

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

beforeEach(() => {
  useInspectorStore.getState().clearInspector();
  mockSearchParams = new URLSearchParams("source=direct_nav");
});

describe("integration: inspector query → populated", () => {
  it("submits a query from the inspector and populates all panels", async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = [];
    globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(
        JSON.stringify({
          query: "hi",
          results: [
            {
              grace_id: "g-1",
              entity_type: "Legal_Entity",
              name: "Acme",
              properties: {},
              rerank_score: 0.8,
              rrf_score: 0.7,
              contributing_strategies: ["graph"],
              hop_distance: null,
            },
          ],
          serialized_context: "ctx",
          serialization_format: "template",
          total_candidates: 1,
          strategy_contributions: { graph: 1 },
          latency_ms: { graph: 100, total: 100 },
          retrieval_mode: "single_round",
          query_intents: [],
          properties_omitted_count: 0,
          multi_hop_proxy_score: 0,
          latency_p95_by_mode_ms: {},
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;
    try {
      withClient(<RetrievalInspector />);
      const input = screen.getByTestId("inspector-query-input");
      fireEvent.change(input, { target: { value: "hi" } });
      fireEvent.submit(input.closest("form")!);

      await waitFor(() =>
        expect(screen.getByTestId("strategy-breakdown-chart")).toBeTruthy(),
      );
      expect(screen.getByTestId("results-ranked-list")).toBeTruthy();
      expect(screen.getByTestId("serialized-context-verbatim")).toBeTruthy();
      // All API traffic targets the same /api/retrieval/query path; no
      // disallowed third-party host (EC-7 airgap).
      const urls = calls.map((c) => new URL(c.url));
      expect(
        urls.every(
          (u) =>
            u.hostname === "127.0.0.1" || u.hostname === "localhost",
        ),
      ).toBe(true);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
