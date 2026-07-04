"use client";

import { emitTelemetry } from "@/lib/telemetry/bus";
import { useSessionStore } from "@/lib/state/session-store";
import type { PhaseState } from "@/lib/api/types";

let subscribedSessionId: string | null = null;
let lastPhase: PhaseState = "none";
let lastEnteredAt: string | null = null;

export function startPhaseController(): () => void {
  const unsub = useSessionStore.subscribe((state, prev) => {
    // Session lifecycle transitions — initial activation.
    if (state.sessionId && state.sessionId !== subscribedSessionId) {
      subscribedSessionId = state.sessionId;
      emitTelemetry("session_started", {
        plan_id: null,
        instrument_selected: null,
        rationale_string: null,
      });
    }

    if (state.activePhase !== prev.activePhase) {
      // Exit the previous phase (if any) before entering the next.
      if (lastPhase !== "none" && lastEnteredAt) {
        const exitedAt = new Date().toISOString();
        emitTelemetry("phase_exited", {
          exited_phase: lastPhase,
          exited_at: exitedAt,
          phase_duration_ms: Math.max(
            0,
            Date.parse(exitedAt) - Date.parse(lastEnteredAt),
          ),
          phase_signals_json: {},
        });
      }
      const enteredAt = new Date().toISOString();
      emitTelemetry("phase_entered", {
        entered_phase: state.activePhase,
        entered_at: enteredAt,
      });
      lastPhase = state.activePhase;
      lastEnteredAt = enteredAt;
    }

    if (prev.sessionStatus !== "paused" && state.sessionStatus === "paused") {
      emitTelemetry("session_paused", {
        paused_from_phase: state.activePhase,
        paused_at: state.pausedAt ?? new Date().toISOString(),
      });
    }
    if (prev.sessionStatus === "paused" && state.sessionStatus !== "paused") {
      const pausedAt = prev.pausedAt ?? new Date().toISOString();
      const resumedAt = new Date().toISOString();
      emitTelemetry("session_resumed", {
        resumed_to_phase: state.activePhase,
        resumed_at: resumedAt,
        paused_duration_ms: Math.max(
          0,
          Date.parse(resumedAt) - Date.parse(pausedAt),
        ),
      });
    }
  });
  return unsub;
}

export function resetPhaseControllerForTests() {
  subscribedSessionId = null;
  lastPhase = "none";
  lastEnteredAt = null;
}
