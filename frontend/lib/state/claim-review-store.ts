"use client";
import { create } from "zustand";
import type { AcceptClaimModified, ClaimRecord } from "@/lib/api/types";

// Teach-Back labels per evidence span; the disposition bar gates on every
// span having a non-null label (D226 reuse). Per-span correction text is
// optional; only the label is required to satisfy the gate.
export type TeachBackLabel = "correct" | "wrong" | "missing_something";

type ClaimReviewState = {
  activeClaim: ClaimRecord | null;
  // span_index -> label or null when not yet labelled.
  teachBackLabels: Record<number, TeachBackLabel | null>;
  // span_index -> optional correction text.
  teachBackCorrections: Record<number, string>;
  editDraft: AcceptClaimModified | null;
  editFormOpen: boolean;
  reviewer: string;
};

type ClaimReviewActions = {
  setActiveClaim: (claim: ClaimRecord | null) => void;
  setTeachBackLabel: (spanIndex: number, label: TeachBackLabel | null) => void;
  setTeachBackCorrection: (spanIndex: number, text: string) => void;
  resetTeachBack: () => void;
  setEditDraft: (draft: AcceptClaimModified | null) => void;
  setEditFormOpen: (open: boolean) => void;
  setReviewer: (reviewer: string) => void;
  // Convenience: did the user label every span on the active claim?
  isTeachBackComplete: () => boolean;
};

export type ClaimReviewStore = ClaimReviewState & ClaimReviewActions;

export const useClaimReviewStore = create<ClaimReviewStore>((set, get) => ({
  activeClaim: null,
  teachBackLabels: {},
  teachBackCorrections: {},
  editDraft: null,
  editFormOpen: false,
  reviewer: "",

  setActiveClaim: (claim) =>
    set({
      activeClaim: claim,
      teachBackLabels: {},
      teachBackCorrections: {},
      editDraft: null,
      editFormOpen: false,
    }),
  setTeachBackLabel: (spanIndex, label) =>
    set((s) => ({
      teachBackLabels: { ...s.teachBackLabels, [spanIndex]: label },
    })),
  setTeachBackCorrection: (spanIndex, text) =>
    set((s) => ({
      teachBackCorrections: { ...s.teachBackCorrections, [spanIndex]: text },
    })),
  resetTeachBack: () => set({ teachBackLabels: {}, teachBackCorrections: {} }),
  setEditDraft: (draft) => set({ editDraft: draft }),
  setEditFormOpen: (open) => set({ editFormOpen: open }),
  setReviewer: (reviewer) => set({ reviewer }),
  isTeachBackComplete: () => {
    const { activeClaim, teachBackLabels } = get();
    if (!activeClaim) return false;
    // Empty evidence_spans → trivially complete (D226 edge case in spec).
    if (activeClaim.evidence_spans.length === 0) return true;
    return activeClaim.evidence_spans.every(
      (_, i) => teachBackLabels[i] !== null && teachBackLabels[i] !== undefined,
    );
  },
}));
