"use client";

import { Badge } from "@/components/ui/badge";
import { useSessionStore } from "@/lib/state/session-store";

export function SessionHeader() {
  const sessionId = useSessionStore((s) => s.sessionId);
  const activePhase = useSessionStore((s) => s.activePhase);
  const status = useSessionStore((s) => s.sessionStatus);

  if (!sessionId) return null;

  return (
    <div
      className="flex items-center gap-2 text-xs text-muted-foreground"
      data-testid="session-header"
    >
      <span>Session {sessionId.slice(0, 8)}</span>
      <Badge variant="secondary" className="capitalize" data-phase={activePhase}>
        {activePhase}
      </Badge>
      <Badge variant="outline" data-status={status}>
        {status}
      </Badge>
    </div>
  );
}
