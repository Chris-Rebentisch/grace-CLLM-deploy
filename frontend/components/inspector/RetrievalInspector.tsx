"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { useInspectorStore, type InspectorSource } from "@/lib/state/inspector-store";
import { usePostRetrievalQuery } from "@/lib/query/retrieval";
import type { RetrievalQuery } from "@/lib/api/types";
import { StrategyBreakdownChart } from "./StrategyBreakdownChart";
import { ResultsRankedList } from "./ResultsRankedList";
import { SerializedContextViewer } from "./SerializedContextViewer";
import { SourceTracePanel } from "./SourceTracePanel";
import { LatencyBreakdown } from "./LatencyBreakdown";
import { QueryReplayButton } from "./QueryReplayButton";
import { ReplayCaveatBanner } from "./ReplayCaveatBanner";
import { InspectorEmptyState } from "./InspectorEmptyState";
import { QueryAuditGraph } from "./QueryAuditGraph";

function reconstructRetrievalQuery(queryText: string): RetrievalQuery {
  return {
    query_text: queryText,
    seed_entity_ids: [],
    temporal_start: null,
    temporal_end: null,
    entity_types: [],
    top_k: 10,
    iterative_mode: null,
  };
}

function parseSourceParam(raw: string | null): InspectorSource {
  if (raw === "chat_link" || raw === "direct_nav" || raw === "replay_button") {
    return raw;
  }
  return "direct_nav";
}

export function RetrievalInspector() {
  const searchParams = useSearchParams();
  const setSource = useInspectorStore((s) => s.setSource);
  const setQuery = useInspectorStore((s) => s.setQuery);
  const response = useInspectorStore((s) => s.lastResponse);
  const selectedIndex = useInspectorStore((s) => s.selectedResultIndex);
  const selectResult = useInspectorStore((s) => s.selectResult);
  const mutation = usePostRetrievalQuery();
  const [inputText, setInputText] = useState("");

  // One-shot on-mount logic: parse URL params, populate state, auto-replay
  // when source=chat_link + query is present.
  const [didBootstrap, setDidBootstrap] = useState(false);
  useEffect(() => {
    if (didBootstrap) return;
    const sourceParam = parseSourceParam(searchParams.get("source"));
    setSource(sourceParam);
    // CP8 D215 — `retrieval_inspector_opened` fires once on mount.
    emitTelemetry("retrieval_inspector_opened", {
      source: sourceParam ?? "direct_nav",
    });
    const queryParam = searchParams.get("query");
    if (queryParam && sourceParam === "chat_link") {
      const q = reconstructRetrievalQuery(queryParam);
      setQuery(q);
      mutation.mutate(q);
    }
    setDidBootstrap(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, didBootstrap]);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    setSource("direct_nav");
    const q = reconstructRetrievalQuery(inputText);
    setQuery(q);
    mutation.mutate(q);
  };

  const selectedResult =
    selectedIndex != null && response?.results
      ? response.results[selectedIndex] ?? null
      : null;

  return (
    <div
      data-testid="retrieval-inspector"
      className="flex flex-col h-full bg-slate-50"
    >
      <ReplayCaveatBanner />
      <header className="flex items-center gap-3 px-4 py-2 border-b bg-white">
        <form onSubmit={onSubmit} className="flex-1 flex items-center gap-2">
          <input
            type="text"
            data-testid="inspector-query-input"
            placeholder="Enter a retrieval query…"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            className="flex-1 rounded-md border border-slate-300 px-3 py-1 text-sm"
          />
          <button
            type="submit"
            className="rounded-md bg-slate-800 text-white px-3 py-1 text-xs font-medium disabled:opacity-50"
            disabled={mutation.isPending}
          >
            Run
          </button>
        </form>
        <QueryReplayButton />
      </header>

      <main className="flex-1 overflow-y-auto p-4 space-y-4">
        {mutation.isError && (
          <div
            data-testid="inspector-error"
            className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-md p-3"
          >
            Retrieval failed: {String(mutation.error?.message)}
          </div>
        )}
        {!response ? (
          <InspectorEmptyState />
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <StrategyBreakdownChart
              contributions={response.strategy_contributions}
            />
            <LatencyBreakdown latencyMs={response.latency_ms} />
            <div className="lg:col-span-2">
              <ResultsRankedList
                results={response.results}
                selectedIndex={selectedIndex}
                onSelect={(i) => selectResult(i)}
              />
            </div>
            <SourceTracePanel result={selectedResult} />
            <div className="lg:col-span-1">
              <SerializedContextViewer
                serialized={response.serialized_context}
                format={response.serialization_format}
              />
            </div>
            {response.query_event_id ? (
              <div
                className="lg:col-span-2 h-[320px]"
                data-testid="query-audit-graph-container"
              >
                <QueryAuditGraph queryEventId={response.query_event_id} />
              </div>
            ) : null}
          </div>
        )}
      </main>
    </div>
  );
}
