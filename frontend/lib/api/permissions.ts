/**
 * Permissions API client (Chunk 42, D331/D333/D337/D338).
 *
 * Thin typed wrapper over the 10-route surface mounted at
 * `/api/permissions/*`. Mutating routes (`hypothesis/generate`,
 * `matrix/ratify`, `drift/run`) require `X-Admin-Key` when
 * `GRACE_ADMIN_KEY` is set on the server; loopback dev callers are
 * admitted without the header.
 *
 * D120/D217: any band fields surfaced through this client are label
 * strings only — no numeric distance scores ever cross the API boundary.
 */

import { apiRequest } from "./client";
import type {
  DriftBand,
  DriftQueueRow,
  PermissionMatrixListResponse,
  PermissionMatrixVersion,
} from "./types";

export type RatifyMatrixRequest = {
  matrix: Record<string, unknown>;
  created_by?: string | null;
  version_label?: string | null;
};

export type HypothesisTriggerRequest = {
  evidence_id: string;
  operator?: string | null;
  dry_run?: boolean;
};

export type HypothesisTriggerResponse = {
  run_id: string;
  evidence_id: string;
  pid: number | null;
};

export type DriftRunRequest = {
  observation_time?: string | null;
  dry_run?: boolean;
};

export type DriftRunResponse = {
  job_id: string;
  pid: number | null;
  observation_time: string;
};

export const permissionsApi = {
  getActiveMatrix: () =>
    apiRequest<PermissionMatrixVersion | null>(
      "/api/permissions/matrix/active",
    ),

  listMatrixVersions: (limit = 25) =>
    apiRequest<PermissionMatrixListResponse>(
      `/api/permissions/matrix/versions?limit=${encodeURIComponent(limit)}`,
    ),

  getHypothesisRun: (runId: string) =>
    apiRequest<Record<string, unknown>>(
      `/api/permissions/hypothesis/${encodeURIComponent(runId)}`,
    ),

  triggerHypothesisGeneration: (
    body: HypothesisTriggerRequest,
    opts: { adminKey?: string } = {},
  ) =>
    apiRequest<HypothesisTriggerResponse>(
      "/api/permissions/matrix/hypothesis/generate",
      {
        method: "POST",
        body,
        headers: opts.adminKey ? { "X-Admin-Key": opts.adminKey } : undefined,
      },
    ),

  ratifyMatrix: (
    body: RatifyMatrixRequest,
    opts: { adminKey?: string } = {},
  ) =>
    apiRequest<PermissionMatrixVersion>("/api/permissions/matrix/ratify", {
      method: "POST",
      body,
      headers: opts.adminKey ? { "X-Admin-Key": opts.adminKey } : undefined,
    }),

  listDriftQueue: (filters: { drift_band?: DriftBand; status?: string } = {}) => {
    const qs = new URLSearchParams();
    if (filters.drift_band) qs.set("drift_band", filters.drift_band);
    if (filters.status) qs.set("status", filters.status);
    const suffix = qs.toString();
    return apiRequest<{ queue: DriftQueueRow[]; next_cursor: string | null }>(
      `/api/permissions/drift/queue${suffix ? `?${suffix}` : ""}`,
    );
  },

  runDriftDetector: (
    body: DriftRunRequest = {},
    opts: { adminKey?: string } = {},
  ) =>
    apiRequest<DriftRunResponse>("/api/permissions/drift/run", {
      method: "POST",
      body,
      headers: opts.adminKey ? { "X-Admin-Key": opts.adminKey } : undefined,
    }),

  getEvidenceBundle: (evidenceId: string) =>
    apiRequest<Record<string, unknown>>(
      `/api/permissions/evidence/${encodeURIComponent(evidenceId)}`,
    ),
};
