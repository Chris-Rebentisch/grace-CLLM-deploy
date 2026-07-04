"use client";

import { useSessionStore } from "@/lib/state/session-store";

// D192: sessions are in-memory only. Page refresh clears state. This
// banner announces that on fresh loads so the user isn't confused about
// missing context.
export function SessionClearedBanner() {
  const status = useSessionStore((s) => s.sessionStatus);
  if (status !== "idle") return null;
  return (
    <div
      role="status"
      data-testid="session-cleared-banner"
      className="mx-4 my-2 rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs text-muted-foreground"
    >
      New session. Prior conversation state is not preserved across page
      refreshes in this build.
    </div>
  );
}
