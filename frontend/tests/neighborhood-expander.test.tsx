import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { NeighborhoodExpander } from "@/components/graph/NeighborhoodExpander";

const originalFetch = globalThis.fetch;

function withClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

describe("NeighborhoodExpander", () => {
  it("depth=1 button calls the /neighborhood endpoint with depth=1", async () => {
    const calls: Array<{ url: string }> = [];
    globalThis.fetch = vi.fn(async (url: string) => {
      calls.push({ url });
      return new Response(
        JSON.stringify({
          seed: {},
          neighbors: [{ grace_id: "n1" }],
          edges: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;
    try {
      withClient(<NeighborhoodExpander graceId="seed-1" />);
      fireEvent.click(screen.getByTestId("expand-depth-1"));
      await waitFor(() =>
        expect(screen.getByTestId("expander-summary")).toBeTruthy(),
      );
      expect(calls).toHaveLength(1);
      expect(calls[0].url).toContain("/api/graph/entities/seed-1/neighborhood");
      expect(calls[0].url).toContain("depth=1");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("depth=2 button switches to depth=2 request", async () => {
    const calls: Array<{ url: string }> = [];
    globalThis.fetch = vi.fn(async (url: string) => {
      calls.push({ url });
      return new Response(
        JSON.stringify({
          seed: {},
          neighbors: [{ grace_id: "n1" }, { grace_id: "n2" }],
          edges: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;
    try {
      withClient(<NeighborhoodExpander graceId="seed-1" />);
      fireEvent.click(screen.getByTestId("expand-depth-2"));
      await waitFor(() => {
        const summary = screen.getByTestId("expander-summary").textContent ?? "";
        return summary.includes("neighbors") && summary.includes("edges");
      });
      expect(calls[0].url).toContain("depth=2");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
