import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query/query-client";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useChatStore } from "@/lib/state/chat-store";
import { setChatTransport } from "@/lib/api/transport";
import type {
  CloseConfirmRequest,
  CloseConfirmResponse,
  CloseSummaryRequest,
  CloseSummaryResponse,
  RegenerationQuery,
  RegenerationResponse,
} from "@/lib/api/types";

type Sent = {
  query?: RegenerationQuery;
  closeSummary?: CloseSummaryRequest;
  closeConfirm?: CloseConfirmRequest;
};

function buildAssistant(overrides: Partial<RegenerationResponse> = {}): RegenerationResponse {
  return {
    query: overrides.query ?? "",
    response_text: overrides.response_text ?? "Hi.",
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

beforeEach(() => {
  useChatStore.setState({ messages: [], loading: false, error: null });
});

afterEach(() => {
  setChatTransport(null);
  vi.restoreAllMocks();
});

describe("integration — Open phase", () => {
  it("over a 30s simulated Open phase, zero forbidden attention-stealing events fire (EC-4)", async () => {
    const { clearRecentTelemetry, getRecentTelemetry } = await import(
      "@/lib/telemetry/bus"
    );
    const { useSessionStore } = await import("@/lib/state/session-store");
    clearRecentTelemetry();
    useSessionStore.getState().clearSession();
    useSessionStore.getState().startSession("open");

    // Simulate 30 seconds of passive render updates (positive list only).
    // If the guard were buggy, these would emit violations.
    const { checkMount } = await import("@/lib/phase/open-guard");
    const start = Date.now();
    for (let t = 0; t < 30; t++) {
      const results = [
        checkMount("response_paint"),
        checkMount("a11y_announcement"),
        checkMount("react_reconciliation_remount"),
        checkMount("latency_milestone_update"),
        checkMount("scroll_anchor"),
      ];
      for (const r of results) expect(r.allowed).toBe(true);
    }
    const violations = getRecentTelemetry().filter(
      (e) => e.type === "protocol_violation_detected",
    );
    expect(violations).toHaveLength(0);
    expect(Date.now() - start).toBeLessThan(5_000); // completes quickly
  });

  it("sends phase_state=open with the user query and appends the assistant response", async () => {
    const sent: Sent = {};
    setChatTransport({
      async sendQuery(req) {
        sent.query = req;
        return buildAssistant({
          query: req.query_text,
          response_text: "OK.",
          phase_state: req.phase_state,
        });
      },
      async sendCloseSummary(): Promise<CloseSummaryResponse> {
        throw new Error("not used");
      },
      async sendCloseConfirm(): Promise<CloseConfirmResponse> {
        throw new Error("not used");
      },
    });

    render(withProviders(<ChatPanel phaseState="open" />));

    const input = screen.getByLabelText("Chat input");
    await userEvent.type(input, "what is up");
    await userEvent.keyboard("{Enter}");

    await screen.findByText("OK.");

    expect(sent.query?.phase_state).toBe("open");
    expect(sent.query?.query_text).toBe("what is up");
    expect(sent.query?.retrieval_query).toBeNull();
    expect(sent.query?.overrides).toBeNull();
  });
});
