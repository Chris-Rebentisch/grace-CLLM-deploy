"use client";

import Link from "next/link";
import type { StoredChatMessage } from "@/lib/state/chat-store";
import { ClaimSpanRenderer } from "@/components/provenance/ClaimSpanRenderer";
import { SourcePanel } from "@/components/provenance/SourcePanel";
import { cn } from "@/lib/utils";

export type MessageBubbleProps = {
  message: StoredChatMessage;
  userQueryText?: string | null;
};

// Assistant messages render with ClaimSpanRenderer + SourcePanel (CP6).
// Chunk 28 adds a "View retrieval trace" secondary action on assistant
// messages that links to /inspector?source=chat_link&query=<query_text>.
// User messages stay plain.
export function MessageBubble({ message, userQueryText }: MessageBubbleProps) {
  const isUser = message.role === "user";

  if (isUser) {
    return (
      <div className="flex w-full justify-end" data-role="user">
        <div className="max-w-[85%] rounded-2xl bg-primary px-4 py-2 text-sm leading-relaxed text-primary-foreground">
          <p className="whitespace-pre-wrap">{message.content}</p>
        </div>
      </div>
    );
  }

  const inspectorHref = userQueryText
    ? `/inspector?source=chat_link&query=${encodeURIComponent(userQueryText)}`
    : null;

  return (
    <div className="flex w-full justify-start" data-role="assistant">
      <div
        className={cn(
          "max-w-[85%] rounded-2xl bg-muted px-4 py-2 text-sm leading-relaxed text-foreground",
        )}
      >
        <ClaimSpanRenderer
          responseText={message.content}
          claimSpans={message.claim_spans}
        />
        <SourcePanel message={message} />
        {inspectorHref && (
          <div className="mt-2 text-xs">
            <Link
              href={inspectorHref}
              data-testid="view-retrieval-trace-link"
              className="text-indigo-600 hover:text-indigo-800 underline"
            >
              View retrieval trace →
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
