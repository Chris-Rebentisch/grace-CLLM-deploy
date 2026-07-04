import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { createQueryClient } from "@/lib/query/query-client";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { setChatTransport } from "@/lib/api/transport";
import { useChatStore } from "@/lib/state/chat-store";
import { useSessionStore } from "@/lib/state/session-store";
import { BackendError } from "@/lib/api/errors";
import type { RegenerationResponse } from "@/lib/api/types";

beforeEach(() => {
  useChatStore.setState({ messages: [], loading: false, error: null });
  useSessionStore.getState().clearSession();
});

afterEach(() => {
  setChatTransport(null);
  vi.restoreAllMocks();
});

function renderPanel() {
  const client = createQueryClient();
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <ChatPanel phaseState="open" />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

function buildAssistant(overrides: Partial<RegenerationResponse> = {}): RegenerationResponse {
  return {
    query: "",
    response_text: "Recovered.",
    claim_spans: [],
    phase_state: "open",
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

describe("backend-recovery", () => {
  it("502 then 200 via Retry — user message preserved, no duplicate messages, no orphaned loading", async () => {
    let calls = 0;
    setChatTransport({
      async sendQuery() {
        calls += 1;
        if (calls === 1) {
          throw new BackendError(502, "synthesize", "temporarily unavailable");
        }
        return buildAssistant();
      },
      async sendCloseSummary() {
        throw new Error("unused");
      },
      async sendCloseConfirm() {
        throw new Error("unused");
      },
    });

    renderPanel();
    const input = screen.getByLabelText("Chat input");
    await userEvent.type(input, "will it recover?");
    await userEvent.keyboard("{Enter}");

    // Error surfaces.
    await screen.findByText(/Backend error \(502\)/);

    // User message still present (exactly one).
    const userMessages = screen.getAllByText("will it recover?");
    expect(userMessages).toHaveLength(1);

    // Retry wires to the same last-user message and succeeds.
    await userEvent.click(screen.getByRole("button", { name: /^Retry$/ }));
    await screen.findByText("Recovered.");

    // Only one user message, one assistant message, and no lingering
    // loading state — matches the state-integrity promise in R14.
    expect(screen.getAllByText("will it recover?")).toHaveLength(1);
    expect(useChatStore.getState().loading).toBe(false);
    expect(useChatStore.getState().error).toBeNull();
    expect(useChatStore.getState().messages).toHaveLength(2);
  });
});
