// Chunk 41 D328 — typed client surface for the 10-route Decomposition API.

import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient } from "@/lib/api/client";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function captureFetch(body: unknown = {}) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const fetchSpy = (async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
  globalThis.fetch = fetchSpy;
  return calls;
}

describe("apiClient — Chunk 41 Decomposition surface (D328)", () => {
  it("listDecompositionRuns issues GET /api/decomposition/runs with limit", async () => {
    const calls = captureFetch({ runs: [], next_cursor: null });
    await apiClient.listDecompositionRuns({ limit: 50 });
    expect(calls.length).toBe(1);
    expect(calls[0].url).toMatch(/\/api\/decomposition\/runs\?limit=50/);
    expect(calls[0].init.method ?? "GET").toBe("GET");
  });

  it("triggerDecompositionRun POSTs to /runs/trigger with archive_root", async () => {
    const calls = captureFetch({ run_id: "r1", archive_root: "/tmp/x", archive_root_canonical_hash: "h", pid: 1 });
    await apiClient.triggerDecompositionRun({ archive_root: "/tmp/x" });
    expect(calls[0].url).toMatch(/\/api\/decomposition\/runs\/trigger$/);
    expect(calls[0].init.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init.body))).toEqual({
      archive_root: "/tmp/x",
    });
  });

  it("submitDecompositionLayer5Decision uses path runId + payload mirror", async () => {
    const calls = captureFetch({});
    await apiClient.submitDecompositionLayer5Decision("run-uuid-1", {
      decision_kind: "accepted_segmented",
      rationale: null,
    });
    expect(calls[0].url).toMatch(
      /\/api\/decomposition\/runs\/run-uuid-1\/layer5\/decision$/,
    );
    expect(calls[0].init.method).toBe("POST");
    const body = JSON.parse(String(calls[0].init.body));
    expect(body.decision_kind).toBe("accepted_segmented");
  });

  it("getDecompositionSegmentationMap honors Accept: application/yaml", async () => {
    const calls = captureFetch({});
    await apiClient.getDecompositionSegmentationMap("run-uuid", "map-uuid", true);
    const headers = (calls[0].init.headers ?? {}) as Record<string, string>;
    expect(headers["Accept"]).toBe("application/yaml");
    expect(calls[0].url).toMatch(
      /\/runs\/run-uuid\/segmentation-maps\/map-uuid$/,
    );
  });
});
