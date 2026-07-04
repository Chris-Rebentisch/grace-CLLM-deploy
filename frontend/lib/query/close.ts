"use client";

import { useMutation } from "@tanstack/react-query";
import { getChatTransport } from "@/lib/api/transport";
import type {
  CloseConfirmRequest,
  CloseConfirmResponse,
  CloseSummaryRequest,
  CloseSummaryResponse,
} from "@/lib/api/types";

export function useCloseSummary() {
  return useMutation<CloseSummaryResponse, Error, CloseSummaryRequest>({
    mutationFn: async (req) => {
      const transport = getChatTransport();
      return transport.sendCloseSummary(req);
    },
  });
}

export function useCloseConfirm() {
  return useMutation<CloseConfirmResponse, Error, CloseConfirmRequest>({
    mutationFn: async (req) => {
      const transport = getChatTransport();
      return transport.sendCloseConfirm(req);
    },
  });
}
