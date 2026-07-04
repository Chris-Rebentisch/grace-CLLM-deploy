"use client";

// Chunk 28 D211 — inspector state: last query, last response, replay source.

import { create } from "zustand";
import type { RetrievalQuery, RetrievalResponse } from "@/lib/api/types";

export type InspectorSource =
  | "chat_link"
  | "direct_nav"
  | "replay_button"
  | null;

type InspectorStoreState = {
  lastQuery: RetrievalQuery | null;
  lastResponse: RetrievalResponse | null;
  source: InspectorSource;
  selectedResultIndex: number | null;
};

type InspectorStoreActions = {
  setQuery: (q: RetrievalQuery | null) => void;
  setResponse: (r: RetrievalResponse | null) => void;
  setSource: (s: InspectorSource) => void;
  selectResult: (index: number | null) => void;
  clearInspector: () => void;
};

export type InspectorStore = InspectorStoreState & InspectorStoreActions;

const INITIAL_STATE: InspectorStoreState = {
  lastQuery: null,
  lastResponse: null,
  source: null,
  selectedResultIndex: null,
};

export const useInspectorStore = create<InspectorStore>((set) => ({
  ...INITIAL_STATE,
  setQuery: (q) => set({ lastQuery: q }),
  setResponse: (r) => set({ lastResponse: r }),
  setSource: (s) => set({ source: s }),
  selectResult: (i) => set({ selectedResultIndex: i }),
  clearInspector: () => set(INITIAL_STATE),
}));
