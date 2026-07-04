"use client";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import type {
  AcceptClaimRequest,
  ClaimListFilters,
  RejectClaimRequest,
} from "@/lib/api/types";

export function useClaims(filters: ClaimListFilters = {}, cursor: string | null = null, limit = 25) {
  return useQuery({
    queryKey: ["claims", filters, cursor, limit],
    queryFn: () => apiClient.getClaims(filters, cursor, limit),
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: false,
  });
}

export function useAcceptClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { claimId: string; body: AcceptClaimRequest }) =>
      apiClient.acceptClaim(args.claimId, args.body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
    },
  });
}

export function useRejectClaim() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (args: { claimId: string; body: RejectClaimRequest }) =>
      apiClient.rejectClaim(args.claimId, args.body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["claims"] });
    },
  });
}

export function useEditAndAcceptClaim() {
  // Edit-and-Accept is a flavour of accept where modified_claim is non-null;
  // the backend handles supersession atomically. This is a thin alias to
  // useAcceptClaim to make the call site at the disposition bar self-documenting.
  return useAcceptClaim();
}
