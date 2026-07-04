import { beforeEach, describe, expect, it } from "vitest";
import { useInspectorStore } from "@/lib/state/inspector-store";
import type { RetrievalQuery, RetrievalResponse } from "@/lib/api/types";

const Q: RetrievalQuery = {
  query_text: "smoke test",
  seed_entity_ids: [],
  temporal_start: null,
  temporal_end: null,
  entity_types: [],
  top_k: 10,
  iterative_mode: null,
};

const R: RetrievalResponse = {
  query: "smoke test",
  results: [],
  serialized_context: "",
  serialization_format: "template",
  total_candidates: 0,
  strategy_contributions: { graph: 2, semantic: 1 },
  latency_ms: { graph: 50, semantic: 80 },
  retrieval_mode: "single_round",
  query_intents: [],
  properties_omitted_count: 0,
  multi_hop_proxy_score: 0,
  latency_p95_by_mode_ms: {},
};

beforeEach(() => {
  useInspectorStore.getState().clearInspector();
});

describe("inspector-store", () => {
  it("setQuery/setResponse/setSource persist within a session", () => {
    useInspectorStore.getState().setQuery(Q);
    useInspectorStore.getState().setResponse(R);
    useInspectorStore.getState().setSource("chat_link");
    expect(useInspectorStore.getState().lastQuery).toEqual(Q);
    expect(useInspectorStore.getState().lastResponse).toEqual(R);
    expect(useInspectorStore.getState().source).toBe("chat_link");
  });

  it("clearInspector returns to the idle state (replay affordance reset)", () => {
    useInspectorStore.getState().setSource("replay_button");
    useInspectorStore.getState().selectResult(3);
    useInspectorStore.getState().clearInspector();
    expect(useInspectorStore.getState().source).toBeNull();
    expect(useInspectorStore.getState().selectedResultIndex).toBeNull();
    expect(useInspectorStore.getState().lastResponse).toBeNull();
  });
});
