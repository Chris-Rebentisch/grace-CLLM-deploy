/**
 * Typed API client for communications routes (Chunk 60, CP2).
 *
 * All outbound requests carry X-Graph-Scope: all via apiRequest.
 */

import { apiRequest } from "./client";

// --- Response types ---

export type ProfileListItem = {
  person_id: string;
  profile_version: number;
  style_signature: Record<string, string> | null;
  profile_quality_band: string;
  created_at: string | null;
};

export type ProfileDetail = {
  person_id: string;
  profile_version: number;
  style_signature: Record<string, string> | null;
  profile_quality_band: string;
  created_at: string | null;
};

export type RecipientProfile = {
  person_id: string;
  recipient_id: string;
  category: string;
  confidence_band: string;
  style_delta: Record<string, string | null> | null;
  style_signature: Record<string, string> | null;
  profile_version: number;
};

export type CategoryProfile = {
  person_id: string;
  category: string;
  recipients: Array<{
    recipient_person_id: string;
    confidence_band: string;
    style_delta: Record<string, string | null> | null;
  }>;
};

export type AggregateProfile = {
  aggregate_segment: string;
  avg_sentence_length_band: string;
  avg_formality_band: string;
  avg_directness_band: string;
  profile_count: number;
};

export type DpiaStatus = {
  attestation_active: boolean;
  valid_until: string | null;
  signed_by: string | null;
};

export type PaginatedResponse<T> = {
  items: T[];
  next_cursor: string | null;
};

// --- Fetch functions ---

export function fetchProfiles(
  cursor?: string,
  limit = 25,
): Promise<PaginatedResponse<ProfileListItem>> {
  const params = new URLSearchParams();
  if (cursor) params.set("cursor", cursor);
  params.set("limit", String(limit));
  return apiRequest<PaginatedResponse<ProfileListItem>>(
    `/api/communications/profiles?${params}`,
  );
}

export function fetchProfile(
  personId: string,
): Promise<ProfileDetail> {
  return apiRequest<ProfileDetail>(
    `/api/communications/profiles/${personId}`,
  );
}

export function fetchRecipientProfile(
  personId: string,
  recipientId: string,
): Promise<RecipientProfile> {
  return apiRequest<RecipientProfile>(
    `/api/communications/profiles/${personId}/for-recipient/${recipientId}`,
  );
}

export function fetchCategoryProfile(
  personId: string,
  category: string,
): Promise<CategoryProfile> {
  return apiRequest<CategoryProfile>(
    `/api/communications/profiles/${personId}/for-category/${category}`,
  );
}

export function fetchAggregateProfile(
  segment: string,
): Promise<AggregateProfile> {
  return apiRequest<AggregateProfile>(
    `/api/communications/profiles/aggregate/${segment}`,
  );
}

export function fetchDpiaStatus(): Promise<DpiaStatus> {
  return apiRequest<DpiaStatus>("/api/communications/dpia/status");
}
