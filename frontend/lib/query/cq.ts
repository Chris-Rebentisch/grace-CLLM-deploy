"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";

export function useCQList(filters?: Record<string, string>) {
  return useQuery({
    queryKey: ["cq-list", filters],
    queryFn: () => apiClient.listCQs(filters),
    refetchOnWindowFocus: false,
  });
}

export function useCQCreate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (cq: Record<string, unknown>) => apiClient.createCQ(cq),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["cq-list"] }); },
  });
}

export function useCQCandidates(sessionId: string | null) {
  return useQuery({
    queryKey: ["cq-candidates", sessionId],
    queryFn: () => apiClient.getCQCandidates(sessionId!),
    enabled: !!sessionId,
    refetchInterval: 2000,
    refetchOnWindowFocus: false,
  });
}

export function useCQCandidateDecision() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (args: { id: string; action: "accept" | "reject" }) =>
      args.action === "accept" ? apiClient.acceptCQCandidate(args.id) : apiClient.rejectCQCandidate(args.id),
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["cq-candidates"] }); },
  });
}
