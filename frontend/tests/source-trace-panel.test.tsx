import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { SourceTracePanel } from "@/components/inspector/SourceTracePanel";
import type { RankedResult } from "@/lib/api/types";

const originalFetch = globalThis.fetch;

function withClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const RESULT: RankedResult = {
  grace_id: "g-ABC",
  entity_type: "Legal_Entity",
  name: "Acme",
  properties: {},
  rerank_score: 0.9,
  rrf_score: 0.8,
  contributing_strategies: ["graph"],
  hop_distance: null,
};

describe("SourceTracePanel", () => {
  it("fetches entity via GET /api/graph/entities/{grace_id} and renders provenance", async () => {
    const calls: string[] = [];
    globalThis.fetch = vi.fn(async (url: string) => {
      calls.push(url);
      return new Response(
        JSON.stringify({
          grace_id: "g-ABC",
          source_document_id: "doc-9",
          extraction_event_id: "evt-9",
          ontology_module: "legal_entity",
          human_validated: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;
    try {
      withClient(<SourceTracePanel result={RESULT} />);
      await waitFor(() =>
        expect(screen.getByTestId("source-trace-panel").textContent).toMatch(
          /doc-9/,
        ),
      );
      expect(calls[0]).toContain("/api/graph/entities/g-ABC");
      // DOM has NO numeric extraction_confidence (not rendered)
      const all = screen.getByTestId("source-trace-panel").textContent ?? "";
      expect(all).toMatch(/evt-9/);
      expect(all).toMatch(/legal_entity/);
      expect(screen.getByTestId("source-trace-human-validated").textContent).toBe(
        "✓ validated",
      );
      expect(all).not.toMatch(/rerank_score/);
      expect(all).not.toMatch(/rrf_score/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("renders empty hint when no result is selected", () => {
    withClient(<SourceTracePanel result={null} />);
    expect(screen.getByTestId("source-trace-empty")).toBeTruthy();
  });
});
