"use client";

import { useCallback, useMemo } from "react";
import { MessageList } from "./MessageList";
import { InputBox } from "./InputBox";
import { ErrorState } from "./ErrorState";
import { LatencyReassurance } from "./LatencyReassurance";
import { useChatStore } from "@/lib/state/chat-store";
import { useSessionStore } from "@/lib/state/session-store";
import { useSendQuery } from "@/lib/query/regeneration";
import { useCloseConfirm, useCloseSummary } from "@/lib/query/close";
import { PauseResumeBar } from "@/components/session/PauseResumeBar";
import { SessionClearedBanner } from "@/components/session/SessionClearedBanner";
import { CertaintyLegend } from "@/components/provenance/CertaintyLegend";
import { SummaryView } from "@/components/session/SummaryView";
import { emitTelemetry } from "@/lib/telemetry/bus";
import type {
  ChatMessage as ApiChatMessage,
  PhaseState,
  SessionSummary,
} from "@/lib/api/types";

export type ChatPanelProps = {
  phaseState?: PhaseState;
};

// `phaseState` is a caller override; when omitted the panel reads the
// active phase from the session-store (CP5 integration). ChatPanel is
// responsible for bootstrapping a session on first query.
export function ChatPanel({ phaseState }: ChatPanelProps) {
  const messages = useChatStore((s) => s.messages);
  const loading = useChatStore((s) => s.loading);
  const error = useChatStore((s) => s.error);
  const appendUserMessage = useChatStore((s) => s.appendUserMessage);
  const appendAssistantMessage = useChatStore((s) => s.appendAssistantMessage);
  const setLoading = useChatStore((s) => s.setLoading);
  const setError = useChatStore((s) => s.setError);
  const clearError = useChatStore((s) => s.clearError);

  const sessionStatus = useSessionStore((s) => s.sessionStatus);
  const activePhase = useSessionStore((s) => s.activePhase);
  const startSession = useSessionStore((s) => s.startSession);

  const resolvedPhase: PhaseState = phaseState ?? (activePhase === "none" ? "open" : activePhase);
  const inClosePhase = resolvedPhase === "close";
  const sessionClosed = sessionStatus === "closed";

  const mutation = useSendQuery();
  const closeSummary = useCloseSummary();
  const closeConfirm = useCloseConfirm();

  const userHistory = useMemo(
    () => messages.filter((m) => m.role === "user").map((m) => m.content),
    [messages],
  );

  const handleSubmit = useCallback(
    (text: string) => {
      if (!text.trim()) return;
      if (sessionStatus === "idle") {
        startSession("open");
      }
      const user = appendUserMessage(text);
      setLoading(true);
      clearError();
      mutation.mutate(
        { query_text: text, phase_state: resolvedPhase },
        {
          onSuccess: (response) => {
            appendAssistantMessage(response);
          },
          onError: (err) => {
            const normalized =
              err && typeof err === "object" && "kind" in err
                ? (err as never)
                : { kind: "unknown" as const, message: err.message };
            setError(normalized);
            // Keep the user message; no auto-retry.
            void user;
          },
        },
      );
    },
    [
      sessionStatus,
      startSession,
      appendUserMessage,
      appendAssistantMessage,
      setLoading,
      setError,
      clearError,
      mutation,
      resolvedPhase,
    ],
  );

  const handleRetry = useCallback(() => {
    const last = [...messages].reverse().find((m) => m.role === "user");
    if (!last) return;
    clearError();
    setLoading(true);
    mutation.mutate(
      { query_text: last.content, phase_state: resolvedPhase },
      {
        onSuccess: (response) => appendAssistantMessage(response),
        onError: (err) => {
          const normalized =
            err && typeof err === "object" && "kind" in err
              ? (err as never)
              : { kind: "unknown" as const, message: err.message };
          setError(normalized);
        },
      },
    );
  }, [messages, clearError, setLoading, mutation, resolvedPhase, appendAssistantMessage, setError]);

  const handleClose = useCallback(() => {
    const state = useSessionStore.getState();
    if (state.activePhase === "close" || state.sessionStatus === "closed") return;
    const currentSessionId = state.sessionId;
    if (!currentSessionId) return;

    const snapshotMessages = toApiMessages(useChatStore.getState().messages);

    // enterPhase fires phase_exited (open) + phase_entered (close) via the
    // phase-controller. That gives us the AC #6 + smoke-test ordering.
    // Reading phaseHistory AFTER the transition is required so the open
    // phase has a non-null duration_ms.
    state.enterPhase("close");
    const phaseDurations = aggregatePhaseDurations(
      useSessionStore.getState().phaseHistory,
    );

    closeSummary.mutate({
      session_id: currentSessionId,
      phase_state: "close",
      messages: snapshotMessages,
      phase_durations_ms: phaseDurations,
    });
  }, [closeSummary]);

  const handleConfirmSave = useCallback(
    async ({
      finalSummary,
      edited,
    }: {
      finalSummary: SessionSummary;
      edited: boolean;
    }) => {
      const state = useSessionStore.getState();
      const currentSessionId = state.sessionId;
      if (!currentSessionId) return;

      await closeConfirm.mutateAsync({
        session_id: currentSessionId,
        final_summary: finalSummary,
        summary_edited: edited,
        summary_rejected: false,
      });

      // Close the close-phase in phaseHistory, emit phase_exited for it,
      // then flip status to closed, then emit session_closed. Order
      // matches the smoke-test EC-7 elicitation_events ordering.
      const beforeExit = useSessionStore.getState();
      const closeEntry = [...beforeExit.phaseHistory]
        .reverse()
        .find((e) => e.phase === "close");
      const closeEnteredAtIso = closeEntry?.entered_at ?? null;

      beforeExit.exitPhase();
      const closeDurationMs = closeEnteredAtIso
        ? Math.max(0, Date.now() - Date.parse(closeEnteredAtIso))
        : 0;
      emitTelemetry("phase_exited", {
        exited_phase: "close",
        exited_at: new Date().toISOString(),
        phase_duration_ms: closeDurationMs,
        phase_signals_json: {},
      });

      // Yield one task so the phase_exited envelope's `emitted_at` is
      // strictly less than the session_closed envelope's — backend
      // timestamps are millisecond-precision only, so back-to-back emits
      // otherwise tie on emitted_at and ORDER BY becomes undefined.
      await new Promise((r) => setTimeout(r, 2));

      useSessionStore.getState().closeSession();

      const sessionStartedAt = beforeExit.startedAt;
      const sessionDurationMs = sessionStartedAt
        ? Math.max(0, Date.now() - Date.parse(sessionStartedAt))
        : 0;
      const phaseDurationDistribution = aggregatePhaseDurations(
        useSessionStore.getState().phaseHistory,
      );
      emitTelemetry("session_closed", {
        summary_edited: edited,
        summary_rejected: false,
        session_duration_ms: sessionDurationMs,
        phase_duration_distribution: phaseDurationDistribution,
      });
    },
    [closeConfirm],
  );

  const handleReturnToChat = useCallback(
    async ({ edited }: { edited: boolean }) => {
      const state = useSessionStore.getState();
      if (!state.sessionId) return;
      const sessionStartedAt = state.startedAt;
      const sessionDurationMs = sessionStartedAt
        ? Math.max(0, Date.now() - Date.parse(sessionStartedAt))
        : 0;

      // Emit BEFORE enterPhase so the bridge tags the event with phase=close
      // (matches the protocol §8.2 envelope for close_returned_to_chat).
      emitTelemetry("close_returned_to_chat", {
        prior_phase: "close",
        resumed_phase: "open",
        summary_discarded: edited,
        session_duration_ms: sessionDurationMs,
      });
      state.enterPhase("open");
      closeSummary.reset();
      closeConfirm.reset();
    },
    [closeSummary, closeConfirm],
  );

  const summary = closeSummary.data?.summary ?? null;
  const closeSummaryError = closeSummary.error;

  return (
    <div
      className="flex h-full flex-col overflow-hidden rounded-xl border border-border bg-background"
      data-phase={resolvedPhase}
    >
      <div className="flex items-center justify-end gap-2 border-b border-border px-4 py-2">
        <PauseResumeBar onClose={handleClose} />
      </div>
      <SessionClearedBanner />
      <CertaintyLegend />
      <div className="flex-1 overflow-y-auto px-4 pt-4">
        {inClosePhase ? (
          summary ? (
            <SummaryView
              summary={summary}
              sessionClosed={sessionClosed}
              onConfirmSave={handleConfirmSave}
              onReturnToChat={handleReturnToChat}
              saving={closeConfirm.isPending}
            />
          ) : closeSummaryError ? (
            <CloseSummaryError
              message={closeSummaryError.message}
              onRetry={handleClose}
            />
          ) : (
            <div
              role="status"
              aria-live="polite"
              data-testid="close-loading"
              className="flex flex-1 items-center justify-center text-sm text-muted-foreground"
            >
              Preparing session summary…
            </div>
          )
        ) : (
          <MessageList messages={messages} />
        )}
      </div>
      {error && !inClosePhase ? (
        <ErrorState error={error} onRetry={handleRetry} onDismiss={clearError} />
      ) : null}
      {!inClosePhase ? (
        <>
          <LatencyReassurance active={loading} />
          <InputBox
            onSubmit={handleSubmit}
            disabled={loading}
            history={userHistory}
          />
        </>
      ) : null}
    </div>
  );
}

function aggregatePhaseDurations(
  history: ReturnType<typeof useSessionStore.getState>["phaseHistory"],
): Record<string, number> {
  const acc: Record<string, number> = {};
  for (const entry of history) {
    if (entry.duration_ms == null) continue;
    acc[entry.phase] = (acc[entry.phase] ?? 0) + entry.duration_ms;
  }
  return acc;
}

function toApiMessages(
  stored: ReturnType<typeof useChatStore.getState>["messages"],
): ApiChatMessage[] {
  return stored.map((m) => ({
    role: m.role,
    content: m.content,
    claim_spans: m.role === "assistant" ? m.claim_spans : null,
    sent_at: m.sent_at,
  }));
}

function CloseSummaryError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      data-testid="close-summary-error"
      className="flex flex-col items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm"
    >
      <p>We couldn&apos;t prepare the session summary. {message}</p>
      <button
        type="button"
        className="text-xs underline"
        onClick={onRetry}
      >
        Try again
      </button>
    </div>
  );
}
