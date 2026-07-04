"use client";

import { useEffect, useState } from "react";

// D198 milestones. Timer is client-side heuristic; upgrade to real backend
// stage events rolls in with the streaming chunk (D190).
export type LatencyMilestone = {
  atMs: number;
  text: string;
};

export const DEFAULT_LATENCY_MILESTONES: LatencyMilestone[] = [
  { atMs: 0, text: "Working on your question…" },
  { atMs: 3_000, text: "Checking sources…" },
  { atMs: 6_000, text: "Synthesizing response…" },
  { atMs: 12_000, text: "Still working (this may take a moment)…" },
];

export type LatencyReassuranceProps = {
  active: boolean;
  milestones?: LatencyMilestone[];
  // Test hook — allows tests to substitute a deterministic clock.
  now?: () => number;
};

function pickMessage(
  milestones: LatencyMilestone[],
  elapsed: number,
): string {
  let current = milestones[0]?.text ?? "Working…";
  for (const m of milestones) {
    if (elapsed >= m.atMs) current = m.text;
  }
  return current;
}

export function LatencyReassurance({
  active,
  milestones = DEFAULT_LATENCY_MILESTONES,
  now = () => Date.now(),
}: LatencyReassuranceProps) {
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!active) {
      setMessage(null);
      return;
    }
    const start = now();
    setMessage(pickMessage(milestones, 0));
    const interval = setInterval(() => {
      setMessage(pickMessage(milestones, now() - start));
    }, 500);
    return () => clearInterval(interval);
  }, [active, milestones, now]);

  if (!active || !message) return null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="px-4 py-2 text-xs text-muted-foreground"
      data-testid="latency-reassurance"
    >
      {message}
    </div>
  );
}
