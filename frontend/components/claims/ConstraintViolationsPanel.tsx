"use client";
import { useState } from "react";

export function ConstraintViolationsPanel({
  violations,
}: {
  violations: Record<string, unknown>[] | null;
}) {
  const [open, setOpen] = useState(false);
  if (!violations || violations.length === 0) return null;
  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      className="rounded border border-border bg-white p-2 text-xs text-slate-700"
      data-testid="constraint-violations-panel"
    >
      <summary className="cursor-pointer font-medium">
        Constraint violations ({violations.length})
      </summary>
      <ul className="mt-2 space-y-1">
        {violations.map((v, i) => (
          <li key={i} data-testid={`constraint-${i}`} className="break-words">
            {JSON.stringify(v)}
          </li>
        ))}
      </ul>
    </details>
  );
}
