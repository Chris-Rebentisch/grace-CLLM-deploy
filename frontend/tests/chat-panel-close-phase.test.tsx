import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query/query-client";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useChatStore } from "@/lib/state/chat-store";
import { useSessionStore } from "@/lib/state/session-store";
import { setChatTransport } from "@/lib/api/transport";
import { startTelemetryBridge } from "@/lib/telemetry/bridge";
import { startPhaseController } from "@/lib/phase/phase-controller";
import {
  clearRecentTelemetry,
  getRecentTelemetry,
} from "@/lib/telemetry/bus";
import type {
  CloseConfirmRequest,
  CloseConfirmResponse,
  CloseSummaryRequest,
  CloseSummaryResponse,
  RegenerationQuery,
  RegenerationResponse,
} from "@/lib/api/types";

type Recorded = {
  query?: RegenerationQuery;
  closeSummary?: CloseSummaryRequest;
  closeConfirm?: CloseConfirmRequest;
  elicitationEvents: string[];
};

function buildAssistant(
  overrides: Partial<RegenerationResponse> = {},
): RegenerationResponse {
  return {
    query: overrides.query ?? "",
    response_text: overrides.response_text ?? "OK.",
    claim_spans: [],
    phase_state: overrides.phase_state ?? "open",
    contributing_grace_ids: [],
    strategy_contributions: {},
    latency_ms: {},
    token_usage: {},
    model: "qwen2.5:7b",
    provider: "ollama",
    retrieval_mode: "single_round",
    response_metadata: {
      context_truncated: false,
      span_detector_mode: "sentence_fallback",
      phase_style_applied: "none",
      span_detection_note: null,
      model_override_applied: false,
    },
    ...overrides,
  };
}

function withProviders(ui: React.ReactElement) {
  const client = createQueryClient();
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider>{ui}</TooltipProvider>
    </QueryClientProvider>
  );
}

const originalFetch = globalThis.fetch;

function installTransportAndFetch(recorded: Recorded) {
  setChatTransport({
    async sendQuery(req) {
      recorded.query = req;
      return buildAssistant({
        query: req.query_text,
        response_text: "Hello there.",
        phase_state: req.phase_state,
      });
    },
    async sendCloseSummary(req): Promise<CloseSummaryResponse> {
      recorded.closeSummary = req;
      return {
        session_id: req.session_id,
        request_id: "req-test-1",
        summary: {
          narrative: "A concise session summary narrative.",
          ontology_changes: [],
          cqs_flipped_state: [],
          decisions_recorded: [],
          deferred_items: [],
          certainty_band_shifts: [],
        },
      };
    },
    async sendCloseConfirm(req): Promise<CloseConfirmResponse> {
      recorded.closeConfirm = req;
      return {
        session_id: req.session_id,
        session_status: "closed",
        recorded_at: new Date().toISOString(),
      };
    },
  });

  globalThis.fetch = (async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.includes("/api/elicitation/events")) {
      try {
        const body = JSON.parse(String(init?.body));
        recorded.elicitationEvents.push(body.event_type);
      } catch {
        // ignore parse errors — defensive only
      }
      return new Response(
        JSON.stringify({ event_id: "ok", accepted_at: new Date().toISOString() }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  useChatStore.setState({ messages: [], loading: false, error: null });
  useSessionStore.getState().clearSession();
  clearRecentTelemetry();
});

afterEach(() => {
  setChatTransport(null);
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
  useSessionStore.getState().clearSession();
});

describe("integration — ChatPanel close phase (Defect 5)", () => {
  it(
    "Close session → summary mutation → SummaryView → confirm → session_closed lands",
    async () => {
      const recorded: Recorded = { elicitationEvents: [] };
      installTransportAndFetch(recorded);

      // Providers wire the telemetry bridge + phase controller for real,
      // so bus events round-trip to the recorded fetch.
      const unsubBridge = startTelemetryBridge();
      const unsubPhase = startPhaseController();

      try {
        render(withProviders(<ChatPanel />));

        // Submit one query to populate messages and bootstrap the session.
        const input = screen.getByLabelText("Chat input");
        await userEvent.type(input, "what is up");
        await userEvent.keyboard("{Enter}");
        await screen.findByText("Hello there.");

        // Click the Close session button.
        const closeButton = screen.getByRole("button", {
          name: /close session/i,
        });
        await userEvent.click(closeButton);

        // SummaryView populates from the mutation. (The loading state
        // flashes too briefly to observe with a synchronous transport.)
        await screen.findByTestId("summary-view");

        // CloseSummary request captured with correct shape.
        expect(recorded.closeSummary?.phase_state).toBe("close");
        expect(recorded.closeSummary?.messages.length).toBe(2);
        expect(typeof recorded.closeSummary?.session_id).toBe("string");
        // phase_durations_ms is keyed by phase name; "open" must have a
        // non-null duration because enterPhase("close") closed it.
        expect(recorded.closeSummary?.phase_durations_ms.open).toBeGreaterThanOrEqual(0);

        // Edit the narrative, then Confirm and Save.
        const textarea = screen.getByTestId("summary-textarea");
        await userEvent.type(textarea, " + operator edit");
        expect(screen.getByTestId("summary-unsaved-indicator")).toBeInTheDocument();

        await userEvent.click(screen.getByTestId("confirm-save"));

        // CloseConfirm request captured with edited=true.
        await waitFor(() => {
          expect(recorded.closeConfirm).toBeDefined();
        });
        expect(recorded.closeConfirm?.summary_edited).toBe(true);
        expect(recorded.closeConfirm?.summary_rejected).toBe(false);
        expect(recorded.closeConfirm?.final_summary.narrative).toContain(
          "operator edit",
        );

        // Session flips to closed via the store.
        await waitFor(() => {
          expect(useSessionStore.getState().sessionStatus).toBe("closed");
        });

        // session_closed landed on the local bus with summary_edited=true.
        const closedEvents = getRecentTelemetry().filter(
          (e) => e.type === "session_closed",
        );
        expect(closedEvents).toHaveLength(1);
        expect(
          (closedEvents[0].payload as { summary_edited: boolean }).summary_edited,
        ).toBe(true);

        // Bridge forwarded the full event sequence to /api/elicitation/events.
        await waitFor(() => {
          expect(recorded.elicitationEvents).toContain("session_closed");
        });
        // Contract: session_started, phase_entered open, phase_exited open,
        // phase_entered close, phase_exited close, session_closed in order.
        const indexOf = (t: string) => recorded.elicitationEvents.indexOf(t);
        expect(indexOf("session_started")).toBeGreaterThanOrEqual(0);
        expect(indexOf("session_started")).toBeLessThan(indexOf("session_closed"));
        expect(indexOf("phase_entered")).toBeLessThan(indexOf("session_closed"));
        // phase_exited emitted at least once (for "open") before session_closed.
        expect(indexOf("phase_exited")).toBeGreaterThanOrEqual(0);
        expect(indexOf("phase_exited")).toBeLessThan(indexOf("session_closed"));
      } finally {
        unsubPhase();
        unsubBridge();
      }
    },
    10_000,
  );

  it("Return to Chat with edits emits close_returned_to_chat and reverts phase to open", async () => {
    const recorded: Recorded = { elicitationEvents: [] };
    installTransportAndFetch(recorded);

    const unsubBridge = startTelemetryBridge();
    const unsubPhase = startPhaseController();

    try {
      render(withProviders(<ChatPanel />));

      // Bootstrap a session + one message exchange.
      const input = screen.getByLabelText("Chat input");
      await userEvent.type(input, "hello");
      await userEvent.keyboard("{Enter}");
      await screen.findByText("Hello there.");

      // Enter close phase.
      await userEvent.click(
        screen.getByRole("button", { name: /close session/i }),
      );
      await screen.findByTestId("summary-view");

      // Edit then click Return to Chat twice (arm + discard).
      await userEvent.type(screen.getByTestId("summary-textarea"), " edits");
      const returnBtn = screen.getByTestId("return-to-chat");
      await userEvent.click(returnBtn);
      expect(returnBtn.textContent).toMatch(/Discard edits/i);
      await userEvent.click(returnBtn);

      // Phase reverts to open; session remains active.
      await waitFor(() => {
        expect(useSessionStore.getState().activePhase).toBe("open");
      });
      expect(useSessionStore.getState().sessionStatus).not.toBe("closed");

      // MessageList returns in place of SummaryView (chat history preserved).
      expect(screen.queryByTestId("summary-view")).toBeNull();
      expect(screen.getByText("Hello there.")).toBeInTheDocument();

      const returned = getRecentTelemetry().filter(
        (e) => e.type === "close_returned_to_chat",
      );
      expect(returned).toHaveLength(1);
      const payload = returned[0].payload as {
        prior_phase: string;
        resumed_phase: string;
        summary_discarded: boolean;
      };
      expect(payload.prior_phase).toBe("close");
      expect(payload.resumed_phase).toBe("open");
      expect(payload.summary_discarded).toBe(true);

      // Bridge forwarded the close_returned_to_chat event.
      await waitFor(() => {
        expect(recorded.elicitationEvents).toContain("close_returned_to_chat");
      });
    } finally {
      unsubPhase();
      unsubBridge();
    }
  });
});
