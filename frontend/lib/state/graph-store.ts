"use client";

// Chunk 28 D214 — viewer UI state (layout, type filters, selection, cursor).
// In-memory only, matching Chunk 27 D192; refresh clears.

import { create } from "zustand";
import type { LayoutName } from "@/lib/graph/layout-adapters";

type GraphStoreState = {
  activeLayout: LayoutName;
  visibleEntityTypes: Set<string>;
  visibleRelationshipTypes: Set<string>;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  paginationCursor: string | null;
};

type GraphStoreActions = {
  setLayout: (layout: LayoutName) => void;
  toggleEntityType: (entityType: string) => void;
  toggleRelationshipType: (relationshipType: string) => void;
  setVisibleEntityTypes: (types: string[]) => void;
  setVisibleRelationshipTypes: (types: string[]) => void;
  selectNode: (nodeId: string | null) => void;
  selectEdge: (edgeId: string | null) => void;
  clearSelection: () => void;
  setCursor: (cursor: string | null) => void;
  resetCursor: () => void;
  reset: () => void;
};

export type GraphStore = GraphStoreState & GraphStoreActions;

const INITIAL_STATE: GraphStoreState = {
  activeLayout: "fcose",
  visibleEntityTypes: new Set(),
  visibleRelationshipTypes: new Set(),
  selectedNodeId: null,
  selectedEdgeId: null,
  paginationCursor: null,
};

export const useGraphStore = create<GraphStore>((set) => ({
  ...INITIAL_STATE,

  setLayout: (layout) => set({ activeLayout: layout }),

  toggleEntityType: (entityType) =>
    set((state) => {
      const next = new Set(state.visibleEntityTypes);
      if (next.has(entityType)) next.delete(entityType);
      else next.add(entityType);
      return { visibleEntityTypes: next };
    }),

  toggleRelationshipType: (relationshipType) =>
    set((state) => {
      const next = new Set(state.visibleRelationshipTypes);
      if (next.has(relationshipType)) next.delete(relationshipType);
      else next.add(relationshipType);
      return { visibleRelationshipTypes: next };
    }),

  setVisibleEntityTypes: (types) =>
    set({ visibleEntityTypes: new Set(types) }),

  setVisibleRelationshipTypes: (types) =>
    set({ visibleRelationshipTypes: new Set(types) }),

  selectNode: (nodeId) => set({ selectedNodeId: nodeId, selectedEdgeId: null }),
  selectEdge: (edgeId) => set({ selectedEdgeId: edgeId, selectedNodeId: null }),
  clearSelection: () => set({ selectedNodeId: null, selectedEdgeId: null }),

  setCursor: (cursor) => set({ paginationCursor: cursor }),
  resetCursor: () => set({ paginationCursor: null }),

  reset: () =>
    set({
      ...INITIAL_STATE,
      visibleEntityTypes: new Set(),
      visibleRelationshipTypes: new Set(),
    }),
}));
