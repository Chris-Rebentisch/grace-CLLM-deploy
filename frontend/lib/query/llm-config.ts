"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import type {
  SaveLLMConfigRequest,
  TestLLMConfigRequest,
} from "@/lib/api/types";

export function useLLMConfig() {
  return useQuery({
    queryKey: ["llm-config"],
    queryFn: () => apiClient.getLLMConfig(),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}

export function useSaveLLMConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SaveLLMConfigRequest) => apiClient.saveLLMConfig(body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["llm-config"] });
    },
  });
}

export function useTestLLMConfig() {
  return useMutation({
    mutationFn: (body: TestLLMConfigRequest) => apiClient.testLLMConfig(body),
  });
}

export function useProviderRegistry() {
  return useQuery({
    queryKey: ["llm-provider-registry"],
    queryFn: () => apiClient.getProviderRegistry(),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
    staleTime: Infinity,
  });
}
