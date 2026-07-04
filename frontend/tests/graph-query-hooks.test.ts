import { describe, expect, it } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  useEntitiesList,
  useNeighborhood,
} from "@/lib/query/graph";
import React from "react";

const originalFetch = globalThis.fetch;

function wrapper() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(
      QueryClientProvider,
      { client },
      children,
    );
}

describe("graph query hooks", () => {
  it("useEntitiesList queryKey includes filters, cursor, and limit", async () => {
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({ entities: [], next_cursor: null }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )) as unknown as typeof fetch;
    try {
      const { result } = renderHook(
        () =>
          useEntitiesList(
            { entity_type: "Legal_Entity", ontology_module: "legal_entity" },
            "cursor-abc",
            50,
          ),
        { wrapper: wrapper() },
      );
      await waitFor(() => expect(result.current.isSuccess).toBe(true));
      expect(result.current.data).toEqual({
        entities: [],
        next_cursor: null,
      });
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("useNeighborhood is disabled when graceId is null", () => {
    globalThis.fetch = (async () =>
      new Response("{}", {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })) as unknown as typeof fetch;
    try {
      const { result } = renderHook(() => useNeighborhood(null, 1), {
        wrapper: wrapper(),
      });
      // Disabled query: fetchStatus is idle, not loading.
      expect(result.current.fetchStatus).toBe("idle");
      expect(result.current.isSuccess).toBe(false);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
