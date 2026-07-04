import { afterEach, describe, expect, it, vi } from "vitest";
import { apiClient, apiRequest } from "@/lib/api/client";
import {
  BackendError,
  ClientError,
  NetworkError,
  TimeoutError,
} from "@/lib/api/errors";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function mockResponse(init: {
  status?: number;
  body?: unknown;
  text?: string;
}): Response {
  const status = init.status ?? 200;
  const body = init.text ?? (init.body !== undefined ? JSON.stringify(init.body) : "");
  return new Response(body, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("apiClient", () => {
  it("injects X-Graph-Scope: all on every request", async () => {
    const spy = vi.fn(async () => mockResponse({ body: { ok: true } }));
    globalThis.fetch = spy as unknown as typeof fetch;

    await apiClient.get("/api/regeneration/config");

    expect(spy).toHaveBeenCalledTimes(1);
    const [, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Graph-Scope"]).toBe("all");
    expect(headers["Accept"]).toBe("application/json");
  });

  it("sets Content-Type and serializes body on POST", async () => {
    const spy = vi.fn(async () => mockResponse({ body: { echo: true } }));
    globalThis.fetch = spy as unknown as typeof fetch;

    const result = await apiClient.post<{ echo: boolean }>(
      "/api/regeneration/query",
      { query_text: "hi" },
    );

    expect(result.echo).toBe(true);
    const [url, init] = spy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/api\/regeneration\/query$/);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );
    expect(init.body).toBe(JSON.stringify({ query_text: "hi" }));
  });

  it("maps 503/500/502 to BackendError with correct stage", async () => {
    const cases: Array<[number, string]> = [
      [503, "retrieve"],
      [500, "assemble"],
      [502, "synthesize"],
    ];
    for (const [status, expectedStage] of cases) {
      globalThis.fetch = (async () =>
        mockResponse({
          status,
          body: { detail: "boom" },
        })) as unknown as typeof fetch;

      await expect(
        apiRequest("/api/regeneration/query", { method: "POST", body: {} }),
      ).rejects.toSatisfy((err: unknown) => {
        expect(err).toBeInstanceOf(BackendError);
        expect((err as BackendError).stage).toBe(expectedStage);
        expect((err as BackendError).status).toBe(status);
        return true;
      });
    }
  });

  it("maps 4xx to ClientError and propagates fetch failures as NetworkError", async () => {
    globalThis.fetch = (async () =>
      mockResponse({
        status: 422,
        body: { detail: "bad input" },
      })) as unknown as typeof fetch;
    await expect(
      apiRequest("/api/regeneration/query", { method: "POST", body: {} }),
    ).rejects.toBeInstanceOf(ClientError);

    globalThis.fetch = (async () => {
      throw new TypeError("network down");
    }) as unknown as typeof fetch;
    await expect(
      apiRequest("/api/regeneration/query", { method: "POST", body: {} }),
    ).rejects.toBeInstanceOf(NetworkError);
  });

  it("raises TimeoutError when the request exceeds timeoutMs", async () => {
    globalThis.fetch = ((_url: string, init?: RequestInit) =>
      new Promise((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        });
      })) as unknown as typeof fetch;

    await expect(
      apiRequest("/api/slow", { method: "GET", timeoutMs: 10 }),
    ).rejects.toBeInstanceOf(TimeoutError);
  });

  // ---------- Chunk 28 typed surface ----------

  it("listEntities issues GET with filters and X-Graph-Scope header", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return mockResponse({ body: { entities: [], next_cursor: null } });
    }) as unknown as typeof fetch;

    await apiClient.listEntities(
      { entity_type: "Legal_Entity", ontology_module: "legal_entity" },
      "abc-cursor",
      50,
    );

    expect(calls).toHaveLength(1);
    const u = new URL(calls[0].url);
    expect(u.pathname).toBe("/api/graph/entities");
    expect(u.searchParams.get("limit")).toBe("50");
    expect(u.searchParams.get("cursor")).toBe("abc-cursor");
    expect(u.searchParams.get("entity_type")).toBe("Legal_Entity");
    expect(u.searchParams.get("ontology_module")).toBe("legal_entity");
    expect(
      (calls[0].init.headers as Record<string, string>)["X-Graph-Scope"],
    ).toBe("all");
  });

  it("getNeighborhood encodes grace_id + depth in the path/query", async () => {
    const calls: Array<{ url: string }> = [];
    globalThis.fetch = (async (url: string) => {
      calls.push({ url });
      return mockResponse({ body: { seed: {}, neighbors: [], edges: [] } });
    }) as unknown as typeof fetch;

    await apiClient.getNeighborhood("grace id with space", 2);

    expect(calls).toHaveLength(1);
    const u = new URL(calls[0].url);
    expect(u.pathname).toBe(
      "/api/graph/entities/grace%20id%20with%20space/neighborhood",
    );
    expect(u.searchParams.get("depth")).toBe("2");
  });

  // ---------- Chunk 29 review/cq/scope methods ----------

  it("getReviewSession carries X-Graph-Scope header", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return mockResponse({ body: { session_id: "s1", status: "IN_PROGRESS" } });
    }) as unknown as typeof fetch;

    await apiClient.getReviewSession("s1");
    expect(calls).toHaveLength(1);
    expect(
      (calls[0].init.headers as Record<string, string>)["X-Graph-Scope"],
    ).toBe("all");
    expect(calls[0].url).toMatch(/\/api\/ontology\/review\/s1$/);
  });

  it("getScopeSegments calls /api/graph/scope/segments with X-Graph-Scope", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return mockResponse({ body: [{ module_name: "finance", entity_count: 10 }] });
    }) as unknown as typeof fetch;

    await apiClient.getScopeSegments();
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toMatch(/\/api\/graph\/scope\/segments$/);
    expect(
      (calls[0].init.headers as Record<string, string>)["X-Graph-Scope"],
    ).toBe("all");
  });

  it("postRetrievalQuery POSTs to /api/retrieval/query with JSON body", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return mockResponse({
        body: {
          query: "hi",
          results: [],
          serialized_context: "",
          serialization_format: "template",
          total_candidates: 0,
          strategy_contributions: {},
          latency_ms: {},
          retrieval_mode: "single_round",
          query_intents: [],
          properties_omitted_count: 0,
          multi_hop_proxy_score: 0,
          latency_p95_by_mode_ms: {},
        },
      });
    }) as unknown as typeof fetch;

    await apiClient.postRetrievalQuery({
      query_text: "hi",
      seed_entity_ids: [],
      temporal_start: null,
      temporal_end: null,
      entity_types: [],
      top_k: 10,
      iterative_mode: null,
    });

    expect(calls).toHaveLength(1);
    const u = new URL(calls[0].url);
    expect(u.pathname).toBe("/api/retrieval/query");
    expect(calls[0].init.method).toBe("POST");
    expect(
      (calls[0].init.headers as Record<string, string>)["Content-Type"],
    ).toBe("application/json");
    expect(JSON.parse(String(calls[0].init.body)).query_text).toBe("hi");
  });

  // ---------- Chunk 43 sensitivity routes ----------

  it("sensitivityApi.generateReport POSTs /api/sensitivity/report/generate with optional admin key", async () => {
    const { sensitivityApi } = await import("@/lib/api/sensitivity");
    const calls: Array<{ url: string; init: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return mockResponse({
        body: {
          report_id: "rpt-1",
          permission_matrix_id: "m-1",
          generated_at: "2026-05-09T00:00:00Z",
          tag_inventory: [],
          coverage_breakdown: [],
          untagged_rules: [],
          truncated: false,
          coverage_band: "high",
          corpus_below_floor: false,
          tag_hygiene_findings: [],
        },
      });
    }) as unknown as typeof fetch;

    await sensitivityApi.generateReport({ force: true, adminKey: "key-xyz" });

    expect(calls).toHaveLength(1);
    const u = new URL(calls[0].url);
    expect(u.pathname).toBe("/api/sensitivity/report/generate");
    expect(u.searchParams.get("force")).toBe("true");
    expect(calls[0].init.method).toBe("POST");
    expect(
      (calls[0].init.headers as Record<string, string>)["X-Admin-Key"],
    ).toBe("key-xyz");
  });

  it("sensitivityApi.listAuditTrail GETs /api/sensitivity/audit-trail with tag query param", async () => {
    const { sensitivityApi } = await import("@/lib/api/sensitivity");
    const calls: Array<{ url: string; init: RequestInit }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init: init ?? {} });
      return mockResponse({ body: { events: [], next_cursor: null } });
    }) as unknown as typeof fetch;

    await sensitivityApi.listAuditTrail({ tag: "pii", matrixId: "m-1" });

    expect(calls).toHaveLength(1);
    const u = new URL(calls[0].url);
    expect(u.pathname).toBe("/api/sensitivity/audit-trail");
    expect(u.searchParams.get("tag")).toBe("pii");
    expect(u.searchParams.get("matrix_id")).toBe("m-1");
    expect(
      (calls[0].init.headers as Record<string, string>)["X-Graph-Scope"],
    ).toBe("all");
  });
});
