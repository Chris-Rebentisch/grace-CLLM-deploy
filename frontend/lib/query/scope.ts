"use client";

import { useQuery } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import type { SegmentRow } from "@/lib/api/types";

export function useScopeSegments() {
  return useQuery<SegmentRow[]>({
    queryKey: ["scope-segments"],
    queryFn: () => apiClient.get<SegmentRow[]>("/api/graph/scope/segments"),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}
