"use client";

import { create } from "zustand";

export type ScopeStoreState = {
  selectedSegments: string[];
  isAllSegments: boolean;
};

type ScopeStoreActions = {
  setSegments: (segments: string[]) => void;
  toggleSegment: (segment: string) => void;
  selectAll: () => void;
  clearSelection: () => void;
  getScopeHeaderValue: () => string;
};

export type ScopeStore = ScopeStoreState & ScopeStoreActions;

export const useScopeStore = create<ScopeStore>((set, get) => ({
  selectedSegments: [],
  isAllSegments: true,

  setSegments(segments: string[]) {
    set({
      selectedSegments: segments,
      isAllSegments: segments.length === 0,
    });
  },

  toggleSegment(segment: string) {
    const current = get().selectedSegments;
    const next = current.includes(segment)
      ? current.filter((s) => s !== segment)
      : [...current, segment];
    set({
      selectedSegments: next,
      isAllSegments: next.length === 0,
    });
  },

  selectAll() {
    set({ selectedSegments: [], isAllSegments: true });
  },

  clearSelection() {
    set({ selectedSegments: [], isAllSegments: true });
  },

  getScopeHeaderValue() {
    const state = get();
    if (state.isAllSegments || state.selectedSegments.length === 0) {
      return "all";
    }
    return `segments:${state.selectedSegments.join(",")}`;
  },
}));
