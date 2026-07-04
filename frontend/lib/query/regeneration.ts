"use client";

import { useMutation } from "@tanstack/react-query";
import { getChatTransport } from "@/lib/api/transport";
import type {
  PhaseState,
  RegenerationQuery,
  RegenerationResponse,
} from "@/lib/api/types";

export type SendQueryInput = {
  query_text: string;
  phase_state: PhaseState;
};

export function buildRegenerationQuery(input: SendQueryInput): RegenerationQuery {
  return {
    query_text: input.query_text,
    retrieval_query: null,
    phase_state: input.phase_state,
    overrides: null,
  };
}

export function useSendQuery() {
  return useMutation<RegenerationResponse, Error, SendQueryInput>({
    mutationFn: async (input) => {
      const transport = getChatTransport();
      return transport.sendQuery(buildRegenerationQuery(input));
    },
  });
}
