"use client";

import { useEffect, useRef } from "react";
import { MessageBubble } from "./MessageBubble";
import type { StoredChatMessage } from "@/lib/state/chat-store";

export type MessageListProps = {
  messages: StoredChatMessage[];
};

export function MessageList({ messages }: MessageListProps) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = endRef.current;
    if (el && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ behavior: "auto", block: "end" });
    }
  }, [messages.length]);

  if (messages.length === 0) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex flex-1 items-center justify-center text-sm text-muted-foreground"
      >
        Ask the graph a question to get started.
      </div>
    );
  }

  // Compute the user query text that preceded each assistant message so
  // MessageBubble can render the Chunk 28 "View retrieval trace" link.
  const priorUserByAssistantId = new Map<string, string>();
  let lastUserContent: string | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      lastUserContent = m.content;
    } else if (m.role === "assistant" && lastUserContent != null) {
      priorUserByAssistantId.set(m.id, lastUserContent);
    }
  }

  return (
    <ol className="flex flex-col gap-3 pb-4" aria-label="Conversation">
      {messages.map((m) => (
        <li key={m.id}>
          <MessageBubble
            message={m}
            userQueryText={
              m.role === "assistant"
                ? priorUserByAssistantId.get(m.id) ?? null
                : null
            }
          />
        </li>
      ))}
      <li aria-hidden="true">
        <div ref={endRef} />
      </li>
    </ol>
  );
}
