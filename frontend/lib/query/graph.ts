"use client";

// Chunk 28 — TanStack Query hooks for the graph viewer + inspector.
// Thin wrappers around `apiClient` so components can subscribe via React
// Query's cache and signal lifecycle (mount/unmount, staleness, errors).

import { useQuery } from "@tanstack/react-query";
import {
  apiClient,
  type ListEntitiesFilters,
  type ListRelationshipsFilters,
} from "@/lib/api/client";
import type {
  EntityRecord,
  NeighborhoodResponse,
  PagedEntitiesResponse,
  PagedRelationshipsResponse,
} from "@/lib/api/types";

export function useGraphInfo() {
  return useQuery<Record<string, unknown>>({
    queryKey: ["graph", "info"],
    queryFn: () => apiClient.getGraphInfo(),
  });
}

export function useEntitiesList(
  filters: ListEntitiesFilters = {},
  cursor: string | null = null,
  limit = 25,
  options: { enabled?: boolean } = {},
) {
  return useQuery<PagedEntitiesResponse>({
    queryKey: [
      "graph",
      "entities",
      filters.entity_type ?? null,
      filters.ontology_module ?? null,
      cursor,
      limit,
    ],
    queryFn: () => apiClient.listEntities(filters, cursor, limit),
    enabled: options.enabled ?? true,
  });
}

export function useEntity(graceId: string | null) {
  return useQuery<EntityRecord | Record<string, unknown>>({
    queryKey: ["graph", "entity", graceId],
    queryFn: () => apiClient.getEntity(graceId as string),
    enabled: !!graceId,
  });
}

export function useNeighborhood(
  graceId: string | null,
  depth: 1 | 2 = 1,
  options: { enabled?: boolean } = {},
) {
  return useQuery<NeighborhoodResponse>({
    queryKey: ["graph", "neighborhood", graceId, depth],
    queryFn: () => apiClient.getNeighborhood(graceId as string, depth),
    enabled: (options.enabled ?? true) && !!graceId,
  });
}

export function useRelationshipsList(
  filters: ListRelationshipsFilters = {},
  cursor: string | null = null,
  limit = 25,
  options: { enabled?: boolean } = {},
) {
  return useQuery<PagedRelationshipsResponse>({
    queryKey: [
      "graph",
      "relationships",
      filters.relationship_type ?? null,
      cursor,
      limit,
    ],
    queryFn: () => apiClient.listRelationships(filters, cursor, limit),
    enabled: options.enabled ?? true,
  });
}
