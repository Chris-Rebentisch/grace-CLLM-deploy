"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";

// The /elements endpoint returns { entity_types: [...], relationships: [...] }
// (each item: { name, status, decision }), but SchemaElementList consumes a flat
// array of { element_type, element_name, decision }. Normalize here so the review
// screen renders regardless of which shape the API hands back. Defensive: if the
// API ever returns a flat array, pass it through unchanged.
export function flattenReviewElements(raw: unknown): Record<string, unknown>[] {
  if (Array.isArray(raw)) return raw as Record<string, unknown>[];
  const d = (raw ?? {}) as {
    entity_types?: Record<string, unknown>[];
    relationships?: Record<string, unknown>[];
  };
  const map = (items: Record<string, unknown>[] | undefined, kind: string) =>
    (items ?? []).map((e) => ({
      ...e,
      element_type: kind,
      element_name: e.element_name ?? e.name,
    }));
  return [
    ...map(d.entity_types, "entity_type"),
    ...map(d.relationships, "relationship_type"),
  ];
}

export function useReviewSession(sessionId: string | null) {
  return useQuery({
    queryKey: ["review-session", sessionId],
    queryFn: () => apiClient.getReviewSession(sessionId!),
    enabled: !!sessionId,
    refetchOnWindowFocus: false,
  });
}

export function useReviewElements(sessionId: string | null) {
  return useQuery({
    queryKey: ["review-elements", sessionId],
    queryFn: () => apiClient.getReviewElements(sessionId!),
    enabled: !!sessionId,
    refetchOnWindowFocus: false,
    select: flattenReviewElements,
  });
}

export function useDecide(sessionId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (decision: Record<string, unknown>) => apiClient.decide(sessionId, decision),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["review-elements", sessionId] });
      void queryClient.invalidateQueries({ queryKey: ["review-progress", sessionId] });
    },
  });
}

export function useCQImpactPreview(sessionId: string | null, elementName: string | null, decision: string | null) {
  return useQuery({
    queryKey: ["cq-impact-preview", sessionId, elementName, decision],
    queryFn: () => apiClient.getCQImpactPreview(sessionId!, elementName!, decision!),
    enabled: !!sessionId && !!elementName && !!decision,
    placeholderData: (prev) => prev,
    refetchOnWindowFocus: false,
  });
}

// D522 session — one-shot assistant turn for the "Something's off?" drawer.
// Stateless mutation: the caller owns the conversation history and passes it in.
export function useAssist(sessionId: string) {
  return useMutation({
    mutationFn: (body: Record<string, unknown>) => apiClient.assistReview(sessionId, body),
  });
}

export function useReviewProgress(sessionId: string | null) {
  return useQuery({
    queryKey: ["review-progress", sessionId],
    queryFn: () => apiClient.getReviewProgress(sessionId!),
    enabled: !!sessionId,
    refetchOnWindowFocus: false,
  });
}
