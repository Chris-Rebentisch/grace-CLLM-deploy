"use client";

import { useSessionStore } from "@/lib/state/session-store";
import { cn } from "@/lib/utils";

// D197 persistent phase banner. Single-line, subtle, NOT attention-stealing.
// Styling chosen: thin border with muted text. No animation, no color shifts.
const PHASE_COPY: Record<string, string> = {
  open: "Open phase active — interruption-free",
  close: "Close phase active — review summary",
  structure: "Structure phase active",
  clarify: "Clarify phase active",
  prepare: "Prepare phase active",
};

export function PhaseBanner() {
  const activePhase = useSessionStore((s) => s.activePhase);
  const status = useSessionStore((s) => s.sessionStatus);

  if (status === "idle") return null;

  let label: string;
  if (status === "paused") {
    label = "Session paused";
  } else if (status === "closed") {
    label = "Session closed";
  } else {
    label = PHASE_COPY[activePhase] ?? `${activePhase} phase active`;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="phase-banner"
      data-phase={activePhase}
      data-status={status}
      className={cn(
        "border-b border-border/60 bg-muted/40 px-4 py-1 text-xs text-muted-foreground",
      )}
    >
      {label}
    </div>
  );
}
