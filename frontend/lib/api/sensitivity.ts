/**
 * Sensitivity Gate API client (Chunk 43, CP6).
 *
 * Thin typed wrapper over the six routes mounted at
 * `/api/sensitivity/*` in `src/api/sensitivity_routes.py`. The
 * generate route is a mutating POST and may require `X-Admin-Key`
 * when `GRACE_ADMIN_KEY` is set; loopback dev callers are admitted
 * without the header.
 *
 * D120/D217: response types omit `coverage_score` — the backend
 * strips it before serialization.
 */

import { apiRequest } from "./client";
import type {
  SensitivityAuditTrailListResponse,
  SensitivityClassificationReportResponse,
  SensitivityReportListResponse,
  TaggedSubset,
} from "./types";

export type GenerateReportOptions = {
  force?: boolean;
  adminKey?: string;
};

export type ListReportsOptions = {
  matrixId: string;
  cursor?: string | null;
  limit?: number;
};

export type AuditTrailFilter = {
  tag: string;
  matrixId?: string | null;
  from?: string | null;
  to?: string | null;
  cursor?: string | null;
  limit?: number;
};

function appendQuery(
  base: string,
  params: Record<string, string | number | undefined | null>,
): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0) return base;
  const qs = entries
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return `${base}?${qs}`;
}

export const sensitivityApi = {
  /** Pure-function projection over the active matrix. Render-only.
   *  Surface helper — no backend route; consumers project locally
   *  from the matrix payload returned by `permissionsApi.getActiveMatrix`. */
  projectTaggedSubset(matrix: {
    schema_version?: string;
    role_clusters?: Array<Record<string, unknown>>;
  }): TaggedSubset {
    const decisions: TaggedSubset["cluster_decisions"] = [];
    const clusters = Array.isArray(matrix.role_clusters)
      ? matrix.role_clusters
      : [];
    for (const cluster of clusters) {
      const accessRules = Array.isArray(
        (cluster as { access_rules?: unknown[] }).access_rules,
      )
        ? ((cluster as { access_rules: Array<Record<string, unknown>> })
            .access_rules)
        : [];
      for (const rule of accessRules) {
        const tags = Array.isArray(
          (rule as { sensitivity_tags?: unknown[] }).sensitivity_tags,
        )
          ? ((rule as { sensitivity_tags: Array<Record<string, unknown>> })
              .sensitivity_tags)
          : [];
        if (tags.length === 0) continue;
        decisions.push({
          cluster_id: String(
            (cluster as { cluster_id?: unknown }).cluster_id ?? "",
          ),
          cluster_display_name: String(
            (cluster as { display_name?: unknown }).display_name ?? "",
          ),
          resource_kind: (rule as { resource_kind: TaggedSubset["cluster_decisions"][number]["resource_kind"] })
            .resource_kind,
          resource_label: String(
            (rule as { resource_label?: unknown }).resource_label ?? "",
          ),
          action: (rule as { action: TaggedSubset["cluster_decisions"][number]["action"] })
            .action,
          decision: (rule as { decision: "allow" | "deny" }).decision,
          sensitivity_tags: tags as TaggedSubset["cluster_decisions"][number]["sensitivity_tags"],
        });
      }
    }
    return {
      matrix_schema_version: String(matrix.schema_version ?? "1.0"),
      cluster_decisions: decisions,
    };
  },

  generateReport: (opts: GenerateReportOptions = {}) => {
    const path = appendQuery("/api/sensitivity/report/generate", {
      force: opts.force ? "true" : undefined,
    });
    return apiRequest<SensitivityClassificationReportResponse>(path, {
      method: "POST",
      body: {},
      headers: opts.adminKey ? { "X-Admin-Key": opts.adminKey } : undefined,
    });
  },

  getLatestReport: () =>
    apiRequest<SensitivityClassificationReportResponse>(
      "/api/sensitivity/report/latest",
    ),

  getReportById: (reportId: string) =>
    apiRequest<SensitivityClassificationReportResponse>(
      `/api/sensitivity/report/${encodeURIComponent(reportId)}`,
    ),

  listReports: (opts: ListReportsOptions) =>
    apiRequest<SensitivityReportListResponse>(
      appendQuery("/api/sensitivity/report", {
        matrix_id: opts.matrixId,
        cursor: opts.cursor ?? undefined,
        limit: opts.limit ?? 25,
      }),
    ),

  listAuditTrail: (filter: AuditTrailFilter) =>
    apiRequest<SensitivityAuditTrailListResponse>(
      appendQuery("/api/sensitivity/audit-trail", {
        tag: filter.tag,
        matrix_id: filter.matrixId ?? undefined,
        from: filter.from ?? undefined,
        to: filter.to ?? undefined,
        cursor: filter.cursor ?? undefined,
        limit: filter.limit ?? 25,
      }),
    ),

  getAuditTrailEvent: (queryEventId: string) =>
    apiRequest<SensitivityAuditTrailRowDetail>(
      `/api/sensitivity/audit-trail/${encodeURIComponent(queryEventId)}`,
    ),
};

/** Detail-shape returned by `GET /audit-trail/{query_event_id}`. CP3
 * ships a 404 skeleton; CP5 lights the body up. The shape here is the
 * forward-compatible declaration so detail consumers compile today. */
export type SensitivityAuditTrailRowDetail = {
  query_event_id: string;
  occurred_at: string;
  sensitivity_tags: string[];
};
