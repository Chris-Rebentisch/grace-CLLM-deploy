import {
  BackendError,
  ClientError,
  NetworkError,
  TimeoutError,
  mapStatusToStage,
} from "./errors";

const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

export function getApiBaseUrl(): string {
  const base =
    (typeof process !== "undefined" &&
      process.env?.NEXT_PUBLIC_GRACE_API_BASE_URL) ||
    DEFAULT_BASE_URL;
  return base.replace(/\/+$/, "");
}

export type RequestOptions = {
  method?: "GET" | "POST" | "PUT" | "DELETE" | "PATCH";
  body?: unknown;
  signal?: AbortSignal;
  headers?: Record<string, string>;
  timeoutMs?: number;
};

const DEFAULT_TIMEOUT_MS = 60_000;

/** Low-level JSON fetch; exported for modules that need custom headers. */
export async function apiRequest<T>(
  path: string,
  opts: RequestOptions = {},
): Promise<T> {
  const base = getApiBaseUrl();
  const url = path.startsWith("http") ? path : `${base}${path}`;
  const method = opts.method ?? "GET";
  const headers: Record<string, string> = {
    Accept: "application/json",
    "X-Graph-Scope": "all",
    ...(opts.headers ?? {}),
  };
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.body);
  }

  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const onAbort = () => controller.abort();
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener("abort", onAbort, { once: true });
  }
  const timer = setTimeout(() => controller.abort("timeout"), timeoutMs);

  let res: Response;
  try {
    res = await fetch(url, { method, headers, body, signal: controller.signal });
  } catch (err) {
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onAbort);
    if (controller.signal.aborted && controller.signal.reason === "timeout") {
      throw new TimeoutError(`Request to ${path} timed out after ${timeoutMs}ms`);
    }
    throw new NetworkError(
      err instanceof Error ? err.message : "fetch failed",
      err,
    );
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener("abort", onAbort);
  }

  if (!res.ok) {
    let parsed: unknown = undefined;
    const raw = await res.text();
    if (raw) {
      try {
        parsed = JSON.parse(raw);
      } catch {
        parsed = raw;
      }
    }
    const msg =
      (parsed && typeof parsed === "object" && "detail" in parsed
        ? String((parsed as { detail: unknown }).detail)
        : undefined) ??
      (parsed && typeof parsed === "object" && "error_message" in parsed
        ? String((parsed as { error_message: unknown }).error_message)
        : undefined) ??
      `Request failed with status ${res.status}`;

    if (res.status >= 500) {
      throw new BackendError(res.status, mapStatusToStage(res.status), msg, parsed);
    }
    throw new ClientError(res.status, msg, parsed);
  }

  // 204 No Content or similar.
  if (res.status === 204) return undefined as unknown as T;
  const text = await res.text();
  if (!text) return undefined as unknown as T;
  try {
    return JSON.parse(text) as T;
  } catch (err) {
    throw new BackendError(
      res.status,
      "assemble",
      "Invalid JSON in successful response",
      err,
    );
  }
}

import type {
  NeighborhoodResponse,
  PagedEntitiesResponse,
  PagedRelationshipsResponse,
  RetrievalQuery,
  RetrievalResponse,
  EntityRecord,
  ClaimListFilters,
  ClaimListResponse,
  AcceptClaimRequest,
  AcceptClaimResponse,
  RejectClaimRequest,
  RejectClaimResponse,
  SourcesScanDirectoryNode,
  ConfigureSourcesRequest,
  ConfigureSourcesResponse,
  BrowseResponse,
  ProcessStartResponse,
  ProcessingStatus,
  CQGenerationStartResponse,
  CQGenerationStatus,
  CQMergeStartResponse,
  CQMergeStatus,
  CQMergeLatest,
  CQSummary,
  SchemaRunStartResponse,
  SchemaRunStatus,
  SeedSchemaData,
  StartReviewRequest,
  ReviewSessionResponse,
  LLMConfig,
  SaveLLMConfigRequest,
  TestLLMConfigRequest,
  TestLLMConfigResponse,
  ProviderRegistryEntry,
  FeedbackRequest,
  FeedbackResponse,
  DecompositionRunDetail,
  Layer5DecisionPayload,
  Layer6ValidationPayload,
  SegmentationMap,
} from "./types";

export type ListEntitiesFilters = {
  entity_type?: string;
  ontology_module?: string;
};

export type ListRelationshipsFilters = {
  relationship_type?: string;
};

function appendParams(path: string, params: Record<string, string | number | undefined>) {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0) return path;
  const qs = entries
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}${qs}`;
}

export const apiClient = {
  get: <T>(path: string, opts: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...opts, method: "GET" }),
  post: <T>(path: string, body: unknown, opts: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...opts, method: "POST", body }),

  // ---------- Chunk 28 graph read surface ----------

  getGraphInfo: () => apiRequest<Record<string, unknown>>("/api/graph/info"),

  listEntities: (
    filters: ListEntitiesFilters = {},
    cursor: string | null = null,
    limit = 25,
  ) =>
    apiRequest<PagedEntitiesResponse>(
      appendParams("/api/graph/entities", {
        limit,
        cursor: cursor ?? undefined,
        entity_type: filters.entity_type,
        ontology_module: filters.ontology_module,
      }),
    ),

  getEntity: (graceId: string) =>
    apiRequest<EntityRecord | Record<string, unknown>>(
      `/api/graph/entities/${encodeURIComponent(graceId)}`,
    ),

  getNeighborhood: (graceId: string, depth: 1 | 2 = 1) =>
    apiRequest<NeighborhoodResponse>(
      appendParams(
        `/api/graph/entities/${encodeURIComponent(graceId)}/neighborhood`,
        { depth },
      ),
    ),

  listRelationships: (
    filters: ListRelationshipsFilters = {},
    cursor: string | null = null,
    limit = 25,
  ) =>
    apiRequest<PagedRelationshipsResponse>(
      appendParams("/api/graph/relationships", {
        limit,
        cursor: cursor ?? undefined,
        relationship_type: filters.relationship_type,
      }),
    ),

  // ---------- Chunk 28 retrieval replay ----------

  postRetrievalQuery: (query: RetrievalQuery) =>
    apiRequest<RetrievalResponse>("/api/retrieval/query", {
      method: "POST",
      body: query,
    }),

  // ---------- Chunk 29 review surface ----------
  getReviewSession: (sessionId: string) =>
    apiRequest<Record<string, unknown>>(`/api/ontology/review/${encodeURIComponent(sessionId)}`),
  getReviewElements: (sessionId: string) =>
    apiRequest<Record<string, unknown>[]>(`/api/ontology/review/${encodeURIComponent(sessionId)}/elements`),
  decide: (sessionId: string, decision: Record<string, unknown>) =>
    apiRequest<Record<string, unknown>>(`/api/ontology/review/${encodeURIComponent(sessionId)}/decide`, { method: "POST", body: decision }),
  getCQImpactPreview: (sessionId: string, elementName: string, hypotheticalDecision: string) =>
    apiRequest<Record<string, unknown>>(appendParams(`/api/ontology/review/${encodeURIComponent(sessionId)}/cq-impact/${encodeURIComponent(elementName)}`, { decision: hypotheticalDecision })),
  getReviewProgress: (sessionId: string) =>
    apiRequest<Record<string, unknown>>(`/api/ontology/review/${encodeURIComponent(sessionId)}/progress`),
  // D522 session — conversational review assistant (read-only LLM explanation).
  assistReview: (sessionId: string, body: Record<string, unknown>) =>
    apiRequest<Record<string, unknown>>(`/api/ontology/review/${encodeURIComponent(sessionId)}/assist`, { method: "POST", body }),
  listCQs: (filters?: Record<string, string>) =>
    apiRequest<Record<string, unknown>[]>(appendParams("/api/discovery/cqs", filters ?? {})),
  createCQ: (cq: Record<string, unknown>) =>
    apiRequest<Record<string, unknown>>("/api/discovery/cqs", { method: "POST", body: cq }),
  getCQCandidates: (sessionId: string) =>
    apiRequest<Record<string, unknown>[]>(appendParams("/api/discovery/cq-candidates", { session_id: sessionId })),
  generateCQCandidates: (body: Record<string, unknown>) =>
    apiRequest<Record<string, unknown>>("/api/discovery/cq-candidates/generate", { method: "POST", body }),
  acceptCQCandidate: (id: string) =>
    apiRequest<Record<string, unknown>>(`/api/discovery/cqs/${encodeURIComponent(id)}/status`, { method: "PUT", body: { status: "approved" } }),
  rejectCQCandidate: (id: string) =>
    apiRequest<Record<string, unknown>>(`/api/discovery/cqs/${encodeURIComponent(id)}/status`, { method: "PUT", body: { status: "rejected" } }),
  getScopeSegments: () =>
    apiRequest<Record<string, unknown>[]>("/api/graph/scope/segments"),

  // ---------- Chunk 30 D230 quarantined-claim review ----------

  getClaims: (filters: ClaimListFilters = {}, cursor: string | null = null, limit = 25) =>
    apiRequest<ClaimListResponse>(
      appendParams("/api/claims", {
        limit,
        cursor: cursor ?? undefined,
        status: filters.status,
        verdict: filters.verdict,
        ontology_module: filters.ontology_module,
        source_document_id: filters.source_document_id,
      }),
    ),

  acceptClaim: (claimId: string, body: AcceptClaimRequest) =>
    apiRequest<AcceptClaimResponse>(
      `/api/claims/${encodeURIComponent(claimId)}/accept`,
      { method: "POST", body },
    ),

  rejectClaim: (claimId: string, body: RejectClaimRequest) =>
    apiRequest<RejectClaimResponse>(
      `/api/claims/${encodeURIComponent(claimId)}/reject`,
      { method: "POST", body },
    ),

  // ---------- Chunk 30 D233 source selector ----------

  scanSources: (rootDir?: string) =>
    apiRequest<SourcesScanDirectoryNode[]>(
      appendParams("/api/discovery/scan-sources", { root_dir: rootDir }),
    ),

  configureSources: (body: ConfigureSourcesRequest) =>
    apiRequest<ConfigureSourcesResponse>("/api/discovery/configure-sources", {
      method: "POST",
      body,
    }),

  // ---------- In-app file browser + processing ----------

  browsePath: (path?: string) =>
    apiRequest<BrowseResponse>(appendParams("/api/discovery/browse", { path })),

  processDocuments: (manifestPath?: string) =>
    apiRequest<ProcessStartResponse>(
      appendParams("/api/discovery/process", { manifest_path: manifestPath }),
      { method: "POST" },
    ),

  getProcessingStatus: () =>
    apiRequest<ProcessingStatus>("/api/discovery/status"),

  // ---------- CQ generation from documents (CQ-first discovery) ----------

  generateCqs: (body?: { passes?: string[]; domains?: string[]; dry_run?: boolean }) =>
    apiRequest<CQGenerationStartResponse>("/api/discovery/generate-cqs", {
      method: "POST",
      body: body ?? {},
    }),

  getGenerationStatus: (runId: string) =>
    apiRequest<CQGenerationStatus>(
      `/api/discovery/generation-status/${encodeURIComponent(runId)}`,
    ),

  cancelGeneration: (runId: string) =>
    apiRequest<{ status: string; run_id: string; message?: string }>(
      `/api/discovery/generate-cqs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    ),

  // Three-tier CQ merge: clusters near-duplicate CQs into a collapsed,
  // schema-only canonical review set. Onboarding auto-runs this after
  // generation so operators review the canonical set, not the raw output.
  mergeCqs: (body?: { dry_run?: boolean }) =>
    apiRequest<CQMergeStartResponse>("/api/discovery/merge-cqs", {
      method: "POST",
      body: body ?? {},
    }),

  getCqMergeStatus: (runId: string) =>
    apiRequest<CQMergeStatus>(
      `/api/discovery/merge-status/${encodeURIComponent(runId)}`,
    ),

  // Latest completed merge run (DB-backed) — the canonical review-set size the
  // onboarding header leads with, instead of the raw CQ row count.
  getLatestCqMerge: () =>
    apiRequest<CQMergeLatest>("/api/discovery/merge-latest"),

  getCqSummary: () => apiRequest<CQSummary>("/api/discovery/cqs/summary"),

  // ---------- Ontology proposal bootstrap (schema extract -> merge -> review) ----------

  extractSchema: (body?: { dry_run?: boolean; domains?: string[]; passes?: string[] }) =>
    apiRequest<SchemaRunStartResponse>("/api/discovery/schema/extract", {
      method: "POST",
      body: body ?? {},
    }),

  getExtractionStatus: (runId: string) =>
    apiRequest<SchemaRunStatus>(
      `/api/discovery/schema/extraction-status/${encodeURIComponent(runId)}`,
    ),

  mergeSchema: (body?: { extraction_run_id?: string; dry_run?: boolean }) =>
    apiRequest<SchemaRunStartResponse>("/api/discovery/schema/merge", {
      method: "POST",
      body: body ?? {},
    }),

  getMergeStatus: (runId: string) =>
    apiRequest<SchemaRunStatus>(
      `/api/discovery/schema/merge-status/${encodeURIComponent(runId)}`,
    ),

  getSeedSchema: (runId: string) =>
    apiRequest<SeedSchemaData>(
      `/api/discovery/schema/seed-schema/${encodeURIComponent(runId)}`,
    ),

  startReview: (body: StartReviewRequest) =>
    apiRequest<ReviewSessionResponse>("/api/ontology/review/start", {
      method: "POST",
      body,
    }),

  // ---------- Chunk 30 D232 LLM config ----------

  getLLMConfig: () => apiRequest<LLMConfig>("/api/llm/config"),

  saveLLMConfig: (body: SaveLLMConfigRequest) =>
    apiRequest<LLMConfig>("/api/llm/config", { method: "POST", body }),

  testLLMConfig: (body: TestLLMConfigRequest) =>
    apiRequest<TestLLMConfigResponse>("/api/llm/config/test", {
      method: "POST",
      body,
    }),

  getProviderRegistry: () =>
    apiRequest<ProviderRegistryEntry[]>("/api/llm/registry"),

  // ---------- Chunk 35a D266 retrieval feedback ----------

  submitRetrievalFeedback: (body: FeedbackRequest) =>
    apiRequest<FeedbackResponse>("/api/feedback/retrieval", {
      method: "POST",
      body,
    }),

  // ---------- Chunk 39 change-directives realization surfaces ----------
  listChangeDirectives: (
    params: {
      limit?: number;
      status?: string;
      tier?: string;
      authored_by?: string;
      velocity_band?: string;
      is_stalled?: boolean;
    } = {},
    headers: Record<string, string> = {},
  ) =>
    apiRequest<{ items: unknown[]; count: number }>(
      appendParams("/api/change-directives", {
        limit: params.limit ?? 200,
        status: params.status,
        tier: params.tier,
        authored_by: params.authored_by,
        velocity_band: params.velocity_band,
        is_stalled:
          params.is_stalled === undefined
            ? undefined
            : params.is_stalled
              ? "true"
              : "false",
      }),
      { headers },
    ),

  getChangeDirective: (directiveId: string, headers: Record<string, string> = {}) =>
    apiRequest<Record<string, unknown>>(
      `/api/change-directives/${encodeURIComponent(directiveId)}`,
      { headers },
    ),

  listChangeDirectiveSnapshots: (
    directiveId: string,
    limit = 30,
    headers: Record<string, string> = {},
  ) =>
    apiRequest<unknown[]>(
      appendParams(
        `/api/change-directives/${encodeURIComponent(directiveId)}/snapshots`,
        { limit },
      ),
      { headers },
    ),

  transitionChangeDirective: (
    directiveId: string,
    body: { to_state: string; reason?: string | null },
    headers: Record<string, string> = {},
  ) =>
    apiRequest<Record<string, unknown>>(
      `/api/change-directives/${encodeURIComponent(directiveId)}/transition`,
      { method: "POST", body, headers },
    ),

  // ---------- Chunk 41 D328 — Decomposition surface (10 routes) ----------

  // 1. GET /api/decomposition/runs
  listDecompositionRuns: (
    params: { cursor?: string | null; limit?: number; status?: string } = {},
  ) =>
    apiRequest<{
      runs: DecompositionRunDetail[];
      next_cursor: string | null;
    }>(
      appendParams("/api/decomposition/runs", {
        cursor: params.cursor ?? undefined,
        limit: params.limit ?? 25,
        status: params.status,
      }),
    ),

  // 2. GET /api/decomposition/runs/{run_id}
  getDecompositionRun: (runId: string) =>
    apiRequest<DecompositionRunDetail>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}`,
    ),

  // 3. GET /api/decomposition/runs/{run_id}/layer4/hypotheses
  getDecompositionLayer4Hypotheses: (runId: string) =>
    apiRequest<{
      run_id: string;
      layer4_hypotheses: Record<string, unknown>;
    }>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/layer4/hypotheses`,
    ),

  // 4. POST /api/decomposition/runs/trigger
  triggerDecompositionRun: (body: {
    archive_root: string;
    operator?: string | null;
    limit?: number | null;
  }) =>
    apiRequest<{
      run_id: string;
      archive_root: string;
      archive_root_canonical_hash: string;
      pid: number | null;
    }>("/api/decomposition/runs/trigger", { method: "POST", body }),

  // 5. POST /api/decomposition/runs/{run_id}/layer5/decision
  submitDecompositionLayer5Decision: (
    runId: string,
    payload: Layer5DecisionPayload,
  ) =>
    apiRequest<DecompositionRunDetail>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/layer5/decision`,
      { method: "POST", body: payload },
    ),

  // 6. POST /api/decomposition/runs/{run_id}/rerun
  triggerDecompositionRerun: (
    runId: string,
    body: { direction: "finer" | "coarser" },
  ) =>
    apiRequest<DecompositionRunDetail>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/rerun`,
      { method: "POST", body },
    ),

  // 7. POST /api/decomposition/runs/{run_id}/layer6/sample-cqs
  generateDecompositionLayer6SampleCqs: (
    runId: string,
    body: {
      segment_name: string;
      document_excerpts?: string[] | null;
      n?: number | null;
    },
  ) =>
    apiRequest<{
      cqs: Array<{
        question: string;
        cq_type: string;
        rationale?: string | null;
      }>;
    }>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/layer6/sample-cqs`,
      { method: "POST", body },
    ),

  // 8. POST /api/decomposition/runs/{run_id}/layer6/validation
  submitDecompositionLayer6Validation: (
    runId: string,
    payload: Layer6ValidationPayload,
  ) =>
    apiRequest<DecompositionRunDetail>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/layer6/validation`,
      { method: "POST", body: payload },
    ),

  // 9. POST /api/decomposition/runs/{run_id}/segmentation-map/ratify
  ratifyDecompositionSegmentationMap: (runId: string, body: SegmentationMap) =>
    apiRequest<{
      segmentation_map_id: string;
      payload_hash: string;
      previous_hash: string | null;
    }>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/segmentation-map/ratify`,
      { method: "POST", body },
    ),

  // 10a. GET /api/decomposition/runs/{run_id}/segmentation-maps
  listDecompositionSegmentationMaps: (runId: string) =>
    apiRequest<{ maps: SegmentationMap[] }>(
      `/api/decomposition/runs/${encodeURIComponent(runId)}/segmentation-maps`,
    ),

  // 10b. GET /api/decomposition/runs/{run_id}/segmentation-maps/{map_id}
  getDecompositionSegmentationMap: (
    runId: string,
    mapId: string,
    asYaml = false,
  ) =>
    asYaml
      ? apiRequest<string>(
          `/api/decomposition/runs/${encodeURIComponent(runId)}/segmentation-maps/${encodeURIComponent(mapId)}`,
          { headers: { Accept: "application/yaml" } },
        )
      : apiRequest<SegmentationMap>(
          `/api/decomposition/runs/${encodeURIComponent(runId)}/segmentation-maps/${encodeURIComponent(mapId)}`,
        ),
};
