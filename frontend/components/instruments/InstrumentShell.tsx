"use client";
import { useRef, useEffect, type ReactNode } from "react";
import { useSessionStore } from "@/lib/state/session-store";

export type InstrumentShellProps = {
  children: ReactNode;
  instrumentName: string;
  onComplete?: () => void;
};

export function InstrumentShell({ children, instrumentName, onComplete }: InstrumentShellProps) {
  const mountedRef = useRef(false);
  const { activePhase } = useSessionStore();

  useEffect(() => {
    if (mountedRef.current) return; // Double-mount dedup (React strict mode)
    mountedRef.current = true;
    return () => { mountedRef.current = false; };
  }, []);

  return (
    <div data-testid={`instrument-shell-${instrumentName}`} className="rounded-lg border border-border bg-white p-4 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-slate-700">{instrumentName}</span>
        <span className="text-[10px] text-slate-400">Phase: {activePhase}</span>
      </div>
      <div>{children}</div>
      {onComplete && (
        <button type="button" onClick={onComplete} data-testid={`instrument-complete-${instrumentName}`} className="mt-3 rounded bg-slate-800 px-3 py-1 text-xs text-white">
          Complete
        </button>
      )}
    </div>
  );
}
