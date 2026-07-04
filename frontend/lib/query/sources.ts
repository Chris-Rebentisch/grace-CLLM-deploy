"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import type { ConfigureSourcesRequest } from "@/lib/api/types";

export function useScanSources(rootDir?: string) {
  return useQuery({
    queryKey: ["sources-scan", rootDir ?? null],
    queryFn: () => apiClient.scanSources(rootDir),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}

export function useConfigureSources() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ConfigureSourcesRequest) => apiClient.configureSources(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources-scan"] });
    },
  });
}

export function useBrowsePath(path?: string) {
  return useQuery({
    queryKey: ["sources-browse", path ?? null],
    queryFn: () => apiClient.browsePath(path),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export function useProcessDocuments() {
  return useMutation({
    mutationFn: (manifestPath?: string) => apiClient.processDocuments(manifestPath),
  });
}

export function useProcessingStatus(enabled: boolean) {
  return useQuery({
    queryKey: ["processing-status"],
    queryFn: () => apiClient.getProcessingStatus(),
    enabled,
    refetchInterval: enabled ? 2000 : false,
    refetchOnWindowFocus: false,
  });
}
