import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query/query-client";
import { setChatTransport } from "@/lib/api/transport";
import { useChatStore } from "@/lib/state/chat-store";
import { useSessionStore } from "@/lib/state/session-store";
import { SummaryView } from "@/components/session/SummaryView";
import { useCloseConfirm, useCloseSummary } from "@/lib/query/close";
import {
  clearRecentTelemetry,
  emitTelemetry,
  getRecentTelemetry,
} from "@/lib/telemetry/bus";
import type {
  CloseConfirmResponse,
  CloseSummaryResponse,
  SessionSummary,
} from "@/lib/api/types";
import { useEffect, useState } from "react";

beforeEach(() => {
  useChatStore.setState({ messages: [], loading: false, error: null });
  useSessionStore.getState().clearSession();
  clearRecentTelemetry();
});

afterEach(() => {
  setChatTransport(null);
  vi.restoreAllMocks();
});

function CloseHost() {
  const summaryMutation = useCloseSummary();
  const confirmMutation = useCloseConfirm();
  const [summary, setSummary] = useState<SessionSummary | null>(null);
  const [closed, setClosed] = useState(false);
  const sessionId = useSessionStore((s) => s.sessionId);
  const activePhase = useSessionStore((s) => s.activePhase);
  const enterPhase = useSessionStore((s) => s.enterPhase);

  useEffect(() => {
    if (activePhase !== "close" || !sessionId || summary) return;
    summaryMutation.mutate(
      {
        session_id: sessionId,
        phase_state: "close",
        messages: [],
        phase_durations_ms: {},
      },
      {
        onSuccess: (res) => setSummary(res.summary),
      },
    );
  }, [activePhase, sessionId, summary, summaryMutation]);

  async function handleConfirm(args: {
    finalSummary: SessionSummary;
    edited: boolean;
  }) {
    if (!sessionId) return;
    await confirmMutation.mutateAsync({
      session_id: sessionId,
      final_summary: args.finalSummary,
      summary_edited: args.edited,
      summary_rejected: false,
    });
    setClosed(true);
    useSessionStore.getState().closeSession();
    emitTelemetry("session_closed", {
      summary_edited: args.edited,
      summary_rejected: false,
      session_duration_ms: 1,
      phase_duration_distribution: {},
    });
  }

  async function handleReturn(args: { edited: boolean }) {
    emitTelemetry("close_returned_to_chat", {
      prior_phase: "close",
      resumed_phase: "open",
      summary_discarded: args.edited,
      session_duration_ms: 1,
    });
    enterPhase("open");
    setSummary(null);
  }

  return (
    <div>
      <button
        type="button"
        data-testid="start-close"
        onClick={() => {
          if (!sessionId) useSessionStore.getState().startSession("open");
          useSessionStore.getState().enterPhase("close");
        }}
      >
        Begin Close
      </button>
      {summary ? (
        <SummaryView
          summary={summary}
          sessionClosed={closed}
          onConfirmSave={handleConfirm}
          onReturnToChat={handleReturn}
        />
      ) : (
        <p data-testid="close-loading">Preparing summary…</p>
      )}
    </div>
  );
}

function renderHost() {
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <CloseHost />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

describe("integration — Close phase", () => {
  it("Confirm-and-Save emits session_closed with summary_edited reflecting user edits", async () => {
    setChatTransport({
      async sendQuery() {
        throw new Error("unused");
      },
      async sendCloseSummary(): Promise<CloseSummaryResponse> {
        return {
          session_id: "abc",
          request_id: "r-1",
          summary: {
            narrative: "Working summary.",
            ontology_changes: [],
            cqs_flipped_state: [],
            decisions_recorded: [],
            deferred_items: [],
            certainty_band_shifts: [],
          },
        };
      },
      async sendCloseConfirm(): Promise<CloseConfirmResponse> {
        return {
          session_id: "abc",
          session_status: "closed",
          recorded_at: new Date().toISOString(),
        };
      },
    });

    renderHost();
    await userEvent.click(screen.getByTestId("start-close"));
    await screen.findByTestId("summary-textarea");

    await userEvent.type(screen.getByTestId("summary-textarea"), " edited");
    await userEvent.click(screen.getByTestId("confirm-save"));

    // session_closed emitted.
    const closed = getRecentTelemetry().filter(
      (e) => e.type === "session_closed",
    );
    expect(closed).toHaveLength(1);
    expect(
      (closed[0].payload as { summary_edited: boolean }).summary_edited,
    ).toBe(true);
  });

  it("Return to Chat with edits emits close_returned_to_chat and keeps the session active", async () => {
    setChatTransport({
      async sendQuery() {
        throw new Error("unused");
      },
      async sendCloseSummary(): Promise<CloseSummaryResponse> {
        return {
          session_id: "abc",
          request_id: "r-1",
          summary: {
            narrative: "Working summary.",
            ontology_changes: [],
            cqs_flipped_state: [],
            decisions_recorded: [],
            deferred_items: [],
            certainty_band_shifts: [],
          },
        };
      },
      async sendCloseConfirm(): Promise<CloseConfirmResponse> {
        throw new Error("not used");
      },
    });

    renderHost();
    await userEvent.click(screen.getByTestId("start-close"));
    await screen.findByTestId("summary-textarea");

    await userEvent.type(screen.getByTestId("summary-textarea"), " edits");
    await userEvent.click(screen.getByTestId("return-to-chat")); // arm
    await userEvent.click(screen.getByTestId("return-to-chat")); // discard

    const events = getRecentTelemetry().filter(
      (e) => e.type === "close_returned_to_chat",
    );
    expect(events).toHaveLength(1);
    const payload = events[0].payload as {
      prior_phase: string;
      resumed_phase: string;
      summary_discarded: boolean;
    };
    expect(payload.prior_phase).toBe("close");
    expect(payload.resumed_phase).toBe("open");
    expect(payload.summary_discarded).toBe(true);
    expect(useSessionStore.getState().sessionStatus).not.toBe("closed");
  });
});
