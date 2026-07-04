"use client";

/**
 * RestorePriorStateDialog — per-tier state preview for disengage (D447).
 *
 * Shows which tiers will be re-enabled and which will remain disabled
 * based on the snapshot captured at engage time. Asymmetric-friction UX
 * per D400: disengage has more friction than engage.
 *
 * D120/D217 — no numeric scores in DOM.
 * EC-12 — all strings from autonomy/copy.ts.
 */

import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

export function RestorePriorStateDialog({
  previousState,
  loading,
  onConfirm,
  onCancel,
}: {
  previousState: Record<string, boolean>;
  loading: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const tiers = Object.entries(previousState).sort(([a], [b]) =>
    a.localeCompare(b),
  );

  return (
    <div
      data-testid="restore-state-dialog"
      className="rounded border border-amber-300 bg-amber-50 p-3"
    >
      <p className="mb-1 text-xs font-medium text-amber-900">
        {AUTONOMY_COPY.restoreStateHeading}
      </p>
      <p className="mb-2 text-xs text-amber-800">
        {AUTONOMY_COPY.restoreStateBody}
      </p>
      <ul className="mb-3 space-y-1">
        {tiers.map(([tier, enabled]) => (
          <li
            key={tier}
            data-testid={`restore-tier-${tier}`}
            className="text-xs text-slate-700"
          >
            <span className="font-medium">{tier}</span>{" "}
            {enabled
              ? AUTONOMY_COPY.restoreStateTierEnabled
              : AUTONOMY_COPY.restoreStateTierDisabled}
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <button
          data-testid="restore-state-cancel"
          onClick={onCancel}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600"
        >
          {AUTONOMY_COPY.restoreStateCancel}
        </button>
        <button
          data-testid="restore-state-confirm"
          disabled={loading}
          onClick={onConfirm}
          className="rounded bg-emerald-600 px-2 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {AUTONOMY_COPY.restoreStateConfirm}
        </button>
      </div>
    </div>
  );
}
