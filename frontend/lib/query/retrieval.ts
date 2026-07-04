"use client";

// Chunk 28 D211 — POST /api/retrieval/query mutation.
// Fires `retrieval_query_replayed` telemetry on success only (spec §18 #7).

import { useMutation } from "@tanstack/react-query";
import { apiClient } from "@/lib/api/client";
import type { RetrievalQuery, RetrievalResponse } from "@/lib/api/types";
import { useInspectorStore } from "@/lib/state/inspector-store";
import { emitTelemetry } from "@/lib/telemetry/bus";

function computeLatencyTotalMs(
  latency_ms: Record<string, number> | undefined,
): number {
  if (!latency_ms) return 0;
  if ("total" in latency_ms && typeof latency_ms.total === "number") {
    return latency_ms.total;
  }
  return Object.values(latency_ms).reduce(
    (acc, v) => acc + (typeof v === "number" ? v : 0),
    0,
  );
}

export function usePostRetrievalQuery() {
  const setResponse = useInspectorStore((s) => s.setResponse);
  const setQuery = useInspectorStore((s) => s.setQuery);

  return useMutation<RetrievalResponse, Error, RetrievalQuery>({
    mutationFn: (query) => apiClient.postRetrievalQuery(query),
    onSuccess: (response, query) => {
      setQuery(query);
      setResponse(response);
      // D215 `retrieval_query_replayed` — fire ONLY on success.
      emitTelemetry("retrieval_query_replayed", {
        strategies_fired: Object.keys(response.strategy_contributions ?? {}),
        latency_ms_total: computeLatencyTotalMs(response.latency_ms),
      });
    },
  });
}
