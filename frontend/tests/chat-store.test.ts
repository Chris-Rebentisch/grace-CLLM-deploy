import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "@/lib/state/chat-store";
import type { RegenerationResponse } from "@/lib/api/types";
import { BackendError } from "@/lib/api/errors";

function buildAssistantResponse(
  overrides: Partial<RegenerationResponse> = {},
): RegenerationResponse {
  return {
    query: "hi",
    response_text: "Hello there.",
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

beforeEach(() => {
  useChatStore.setState({ messages: [], loading: false, error: null });
});

describe("useChatStore", () => {
  it("appends user and assistant messages in order, preserving content and shape", () => {
    const user = useChatStore.getState().appendUserMessage("What is Acme?");
    expect(user.role).toBe("user");
    expect(user.content).toBe("What is Acme?");

    const assistant = useChatStore
      .getState()
      .appendAssistantMessage(
        buildAssistantResponse({ response_text: "A company." }),
      );
    expect(assistant.role).toBe("assistant");
    expect(assistant.content).toBe("A company.");

    const msgs = useChatStore.getState().messages;
    expect(msgs).toHaveLength(2);
    expect(msgs[0].role).toBe("user");
    expect(msgs[1].role).toBe("assistant");
  });

  it("clearChat wipes messages, error, and loading state", () => {
    useChatStore.getState().appendUserMessage("hi");
    useChatStore.getState().setLoading(true);
    useChatStore
      .getState()
      .setError(new BackendError(500, "assemble", "boom"));

    useChatStore.getState().clearChat();

    const state = useChatStore.getState();
    expect(state.messages).toEqual([]);
    expect(state.loading).toBe(false);
    expect(state.error).toBeNull();
  });

  it("setError preserves messages but stops loading; clearError resets error without touching messages", () => {
    useChatStore.getState().appendUserMessage("hi");
    useChatStore.getState().setLoading(true);

    const err = new BackendError(502, "synthesize", "down");
    useChatStore.getState().setError(err);

    let state = useChatStore.getState();
    expect(state.messages).toHaveLength(1);
    expect(state.loading).toBe(false);
    expect(state.error).toBe(err);

    useChatStore.getState().clearError();
    state = useChatStore.getState();
    expect(state.error).toBeNull();
    expect(state.messages).toHaveLength(1);
  });
});
