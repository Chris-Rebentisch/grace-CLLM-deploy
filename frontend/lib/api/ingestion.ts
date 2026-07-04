/**
 * Typed API client for ingestion routes (Chunk 60, CP2).
 *
 * All outbound requests carry X-Graph-Scope: all via apiRequest.
 */

import { apiRequest } from "./client";

// --- Response types ---

export type IngestionSourceItem = {
  id: string;
  name: string;
  source_type: string;
  status: string;
  segment: string;
  config_json: Record<string, unknown>;
  enabled: boolean;
  created_at: string;
  updated_at: string | null;
};

export type IngestionRunItem = {
  id: string;
  source_id: string;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  triage_tier_counts_json: Record<string, number> | null;
  error_message: string | null;
};

export type IngestionEventItem = {
  event_id: string;
  sender_email: string | null;
  subject: string | null;
  sent_at: string | null;
  triage_tier_outcome: string | null;
  message_id: string;
};

export type IngestionConfig = {
  deployment_path: string | null;
  organization_domains: string[];
  tier3_band: "stricter" | "balanced" | "looser";
};

export type PaginatedResponse<T> = {
  items: T[];
  next_cursor: string | null;
};

export type SourceStatusResponse = {
  status: string;
  last_run_at: string | null;
  error_message: string | null;
};

export type OAuthInitResponse = {
  authorize_url: string;
  state: string;
};

// --- Fetch functions ---

export function fetchIngestionConfig(): Promise<IngestionConfig> {
  return apiRequest<IngestionConfig>("/api/ingestion/config");
}

export function fetchIngestionRuns(
  cursor?: string,
  limit = 25,
): Promise<PaginatedResponse<IngestionRunItem>> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  params.set("limit", String(limit));
  return apiRequest<PaginatedResponse<IngestionRunItem>>(
    `/api/ingestion/runs?${params}`,
  );
}

export function fetchIngestionSources(
  cursor?: string,
  limit = 25,
): Promise<PaginatedResponse<IngestionSourceItem>> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  params.set("limit", String(limit));
  return apiRequest<PaginatedResponse<IngestionSourceItem>>(
    `/api/ingestion/sources?${params}`,
  );
}

export function fetchIngestionSource(
  sourceId: string,
): Promise<IngestionSourceItem> {
  return apiRequest<IngestionSourceItem>(`/api/ingestion/sources/${sourceId}`);
}

export function fetchSourceStatus(
  sourceId: string,
): Promise<SourceStatusResponse> {
  return apiRequest<SourceStatusResponse>(
    `/api/ingestion/sources/${sourceId}/status`,
  );
}

export function fetchSourceEvents(
  sourceId: string,
  cursor?: string,
  limit = 50,
): Promise<PaginatedResponse<IngestionEventItem>> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  params.set("limit", String(limit));
  return apiRequest<PaginatedResponse<IngestionEventItem>>(
    `/api/ingestion/sources/${sourceId}/events?${params}`,
  );
}

export function curateEmails(body: {
  source_id: string;
  selected_message_ids: string[];
  deployment_path: string;
}): Promise<unknown> {
  return apiRequest("/api/ingestion/curate", {
    method: "POST",
    body,
  });
}

export function patchOrganizationDomains(
  domains: string[],
): Promise<{ organization_domains: string[] }> {
  return apiRequest("/api/ingestion/config/organization-domains", {
    method: "PATCH",
    body: { organization_domains: domains },
  });
}

export function patchTier3Threshold(
  band: "stricter" | "balanced" | "looser",
): Promise<{ tier3_band: string }> {
  return apiRequest("/api/ingestion/config/tier3-threshold", {
    method: "PATCH",
    body: { tier3_band: band },
  });
}

export function patchDeploymentPath(
  path: string | null,
): Promise<{ deployment_path: string | null }> {
  return apiRequest("/api/ingestion/config/deployment-path", {
    method: "PATCH",
    body: { deployment_path: path },
  });
}

export function fetchOAuthInit(
  provider: string,
): Promise<OAuthInitResponse> {
  return apiRequest<OAuthInitResponse>(
    `/api/ingestion/oauth/init/${provider}`,
  );
}
