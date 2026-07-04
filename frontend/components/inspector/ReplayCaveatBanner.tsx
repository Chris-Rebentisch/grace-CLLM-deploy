"use client";

// D211 locked copy — render ONLY when source === "replay_button" || "chat_link".

import { useInspectorStore } from "@/lib/state/inspector-store";

const BANNER_COPY =
  "This is a replay of the retrieval pipeline. If graph state has changed since the original query, results may differ from what contributed to the original response.";

export function ReplayCaveatBanner() {
  const source = useInspectorStore((s) => s.source);

  // Explicit dual-arm check (JS footgun: `source === "a" || "b"` is truthy).
  if (source !== "replay_button" && source !== "chat_link") {
    return null;
  }

  return (
    <div
      data-testid="replay-caveat-banner"
      className="border-l-4 border-amber-400 bg-amber-50 text-amber-900 px-4 py-2 text-xs"
      role="note"
    >
      {BANNER_COPY}
    </div>
  );
}
