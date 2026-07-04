"use client";
import { create } from "zustand";
import type { ReviewDecisionType } from "@/lib/api/types";

type ReviewStoreState = {
  sessionId: string | null;
  currentDecision: { elementName: string; decision: ReviewDecisionType } | null;
  hoverElement: string | null;
  hoverDecision: ReviewDecisionType | null;
  instrumentModalOpen: boolean;
  activeInstrument: "laddering" | "card_sort" | "teach_back" | null;
};

type ReviewStoreActions = {
  setSessionId: (id: string | null) => void;
  setCurrentDecision: (d: ReviewStoreState["currentDecision"]) => void;
  setHover: (element: string | null, decision: ReviewDecisionType | null) => void;
  openInstrument: (instrument: ReviewStoreState["activeInstrument"]) => void;
  closeInstrument: () => void;
};

export type ReviewStore = ReviewStoreState & ReviewStoreActions;

export const useReviewStore = create<ReviewStore>((set) => ({
  sessionId: null,
  currentDecision: null,
  hoverElement: null,
  hoverDecision: null,
  instrumentModalOpen: false,
  activeInstrument: null,
  setSessionId: (id) => set({ sessionId: id }),
  setCurrentDecision: (d) => set({ currentDecision: d }),
  setHover: (element, decision) => set({ hoverElement: element, hoverDecision: decision }),
  openInstrument: (instrument) => set({ instrumentModalOpen: true, activeInstrument: instrument }),
  closeInstrument: () => set({ instrumentModalOpen: false, activeInstrument: null }),
}));
