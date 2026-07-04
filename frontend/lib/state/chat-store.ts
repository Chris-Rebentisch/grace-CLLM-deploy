"use client";

import { create } from "zustand";
import type { ClaimSpan, RegenerationResponse } from "@/lib/api/types";
import type { BackendError, ClientError, NetworkError, TimeoutError } from "@/lib/api/errors";

export type ChatMessageRole = "user" | "assistant";

export type ChatMessageBase = {
  id: string;
  role: ChatMessageRole;
  content: string;
  sent_at: string;
};

export type UserChatMessage = ChatMessageBase & { role: "user" };

export type AssistantChatMessage = ChatMessageBase & {
  role: "assistant";
  claim_spans: ClaimSpan[];
  response_metadata?: RegenerationResponse["response_metadata"];
  model?: string;
  provider?: string;
  strategy_contributions?: Record<string, number>;
  latency_ms?: Record<string, number>;
};

export type StoredChatMessage = UserChatMessage | AssistantChatMessage;

export type ChatError =
  | BackendError
  | ClientError
  | NetworkError
  | TimeoutError
  | { kind: "unknown"; message: string };

type ChatStoreState = {
  messages: StoredChatMessage[];
  loading: boolean;
  error: ChatError | null;
};

type ChatStoreActions = {
  appendUserMessage: (content: string) => UserChatMessage;
  appendAssistantMessage: (
    response: RegenerationResponse,
  ) => AssistantChatMessage;
  setError: (err: ChatError | null) => void;
  clearError: () => void;
  setLoading: (loading: boolean) => void;
  clearChat: () => void;
};

export type ChatStore = ChatStoreState & ChatStoreActions;

function makeId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export const useChatStore = create<ChatStore>((set) => ({
  messages: [],
  loading: false,
  error: null,
  appendUserMessage(content) {
    const msg: UserChatMessage = {
      id: makeId(),
      role: "user",
      content,
      sent_at: new Date().toISOString(),
    };
    set((s) => ({
      messages: [...s.messages, msg],
      error: null,
    }));
    return msg;
  },
  appendAssistantMessage(response) {
    const msg: AssistantChatMessage = {
      id: makeId(),
      role: "assistant",
      content: response.response_text,
      sent_at: new Date().toISOString(),
      claim_spans: response.claim_spans ?? [],
      response_metadata: response.response_metadata,
      model: response.model,
      provider: response.provider,
      strategy_contributions: response.strategy_contributions,
      latency_ms: response.latency_ms,
    };
    set((s) => ({
      messages: [...s.messages, msg],
      loading: false,
    }));
    return msg;
  },
  setError(err) {
    set({ error: err, loading: false });
  },
  clearError() {
    set({ error: null });
  },
  setLoading(loading) {
    set({ loading });
  },
  clearChat() {
    set({ messages: [], error: null, loading: false });
  },
}));

// Convenience selectors — keep the API small.
export function getChatMessages(): StoredChatMessage[] {
  return useChatStore.getState().messages;
}
