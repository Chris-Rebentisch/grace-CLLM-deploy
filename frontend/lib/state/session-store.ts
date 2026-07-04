"use client";

import { create } from "zustand";
import type { PhaseState } from "@/lib/api/types";
import { newSessionId } from "@/lib/ids/session-id";

export type SessionStatus = "idle" | "active" | "paused" | "resumed" | "closed";

export type PhaseHistoryEntry = {
  phase: PhaseState;
  entered_at: string;
  exited_at: string | null;
  duration_ms: number | null;
};

type SessionStoreState = {
  sessionId: string | null;
  activePhase: PhaseState;
  phaseHistory: PhaseHistoryEntry[];
  sessionStatus: SessionStatus;
  pausedAt: string | null;
  resumedFrom: PhaseState | null;
  startedAt: string | null;
  legendCollapsedThisSession: boolean;
};

type SessionStoreActions = {
  startSession: (initialPhase?: PhaseState) => string;
  setSessionId: (id: string) => void;
  enterPhase: (phase: PhaseState) => void;
  exitPhase: () => void;
  pauseSession: () => void;
  resumeSession: () => void;
  closeSession: () => void;
  clearSession: () => void;
  setLegendCollapsed: (collapsed: boolean) => void;
};

export type SessionStore = SessionStoreState & SessionStoreActions;

const INITIAL_STATE: SessionStoreState = {
  sessionId: null,
  activePhase: "none",
  phaseHistory: [],
  sessionStatus: "idle",
  pausedAt: null,
  resumedFrom: null,
  startedAt: null,
  legendCollapsedThisSession: false,
};

function now(): string {
  return new Date().toISOString();
}

export const useSessionStore = create<SessionStore>((set, get) => ({
  ...INITIAL_STATE,
  // D365 — deep-link hydration: set an existing session ID from
  // URL query params without generating a new one (Chunk 44, CP5).
  setSessionId(id: string) {
    set({
      sessionId: id,
      sessionStatus: "active",
      startedAt: now(),
    });
  },
  startSession(initialPhase: PhaseState = "open") {
    const sessionId = newSessionId();
    const startedAt = now();
    set({
      sessionId,
      sessionStatus: "active",
      startedAt,
      activePhase: initialPhase,
      phaseHistory: [
        { phase: initialPhase, entered_at: startedAt, exited_at: null, duration_ms: null },
      ],
      pausedAt: null,
      resumedFrom: null,
      legendCollapsedThisSession: false,
    });
    return sessionId;
  },
  enterPhase(phase) {
    const state = get();
    const enteredAt = now();
    // Close the active phase first if any.
    const history = [...state.phaseHistory];
    const last = history[history.length - 1];
    if (last && last.exited_at === null) {
      const exitedAt = enteredAt;
      history[history.length - 1] = {
        ...last,
        exited_at: exitedAt,
        duration_ms: Math.max(
          0,
          Date.parse(exitedAt) - Date.parse(last.entered_at),
        ),
      };
    }
    history.push({ phase, entered_at: enteredAt, exited_at: null, duration_ms: null });
    set({ activePhase: phase, phaseHistory: history });
  },
  exitPhase() {
    const state = get();
    const history = [...state.phaseHistory];
    const last = history[history.length - 1];
    if (last && last.exited_at === null) {
      const exitedAt = now();
      history[history.length - 1] = {
        ...last,
        exited_at: exitedAt,
        duration_ms: Math.max(
          0,
          Date.parse(exitedAt) - Date.parse(last.entered_at),
        ),
      };
    }
    set({ phaseHistory: history });
  },
  pauseSession() {
    if (get().sessionStatus === "paused") return;
    set({ sessionStatus: "paused", pausedAt: now() });
  },
  resumeSession() {
    const state = get();
    if (state.sessionStatus !== "paused") return;
    // EC-5: no cooldown, no decay, no penalty. Just restore active status.
    set({
      sessionStatus: "resumed",
      resumedFrom: state.activePhase,
      pausedAt: null,
    });
    // Immediately return to "active" so the resumed state is just an
    // informational affordance, not a sticky mode.
    queueMicrotask(() => {
      if (useSessionStore.getState().sessionStatus === "resumed") {
        useSessionStore.setState({ sessionStatus: "active" });
      }
    });
  },
  closeSession() {
    set({ sessionStatus: "closed" });
  },
  clearSession() {
    set({ ...INITIAL_STATE });
  },
  setLegendCollapsed(collapsed) {
    set({ legendCollapsedThisSession: collapsed });
  },
}));
