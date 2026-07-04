import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { MessageList } from "@/components/chat/MessageList";
import { RetrievalInspector } from "@/components/inspector/RetrievalInspector";
import { useInspectorStore } from "@/lib/state/inspector-store";
import type { StoredChatMessage } from "@/lib/state/chat-store";

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

const MESSAGES: StoredChatMessage[] = [
  {
    id: "u-1",
    role: "user",
    content: "what owns the Main Street property?",
    sent_at: new Date().toISOString(),
  },
  {
    id: "a-1",
    role: "assistant",
    content: "Acme owns Main Street.",
    sent_at: new Date().toISOString(),
    claim_spans: [],
  },
];

beforeEach(() => {
  useInspectorStore.getState().clearInspector();
});

describe("integration: chat → inspector replay", () => {
  it("clicking 'View retrieval trace' link populates the inspector via replay", async () => {
    // Render chat list — link should be present
    withClient(<MessageList messages={MESSAGES} />);
    const link = screen.getByTestId("view-retrieval-trace-link") as HTMLAnchorElement;
    expect(link).toBeTruthy();
    // Link target encodes source=chat_link + query
    expect(link.href).toContain("/inspector");
    expect(link.href).toContain("source=chat_link");
    expect(link.href).toContain(
      `query=${encodeURIComponent("what owns the Main Street property?")}`,
    );

    // Now simulate landing on the inspector with those params — verify
    // the inspector auto-replays and populates.
    mockSearchParams = new URLSearchParams(
      `source=chat_link&query=${encodeURIComponent("what owns the Main Street property?")}`,
    );
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            query: "what owns the Main Street property?",
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
            latency_ms: { total: 50 },
            retrieval_mode: "single_round",
            query_intents: [],
            properties_omitted_count: 0,
            multi_hop_proxy_score: 0,
            latency_p95_by_mode_ms: {},
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    ) as unknown as typeof fetch;
    try {
      withClient(<RetrievalInspector />);
      await waitFor(() =>
        expect(screen.getByTestId("replay-caveat-banner")).toBeTruthy(),
      );
      // Inspector populated with replayed results
      await waitFor(() =>
        expect(screen.getByTestId("results-ranked-list")).toBeTruthy(),
      );
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
