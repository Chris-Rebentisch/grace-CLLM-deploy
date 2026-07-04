"use client";

/**
 * DriftQueueRow — queued classification row with operator decision menu.
 *
 * Renders a single `permission_drift_queue` row. Three-band labels only
 * (D120/D217 — never the underlying kNN distance). Operator actions
 * (`accept`, `defer`, `reject`) render only when the host passes `onDecide`;
 * otherwise pending rows show `PERMISSIONS_COPY.driftQueueRunbookHint`.
 */

import type { DriftBand, DriftQueueRow as DriftQueueRowT } from "@/lib/api/types";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";

const BAND_COPY: Record<DriftBand, string> = {
  high: PERMISSIONS_COPY.driftBandHigh,
  medium: PERMISSIONS_COPY.driftBandMedium,
  low: PERMISSIONS_COPY.driftBandLow,
};

const BAND_CLASSES: Record<DriftBand, string> = {
  high: "border-emerald-500 bg-emerald-50 text-emerald-900",
  medium: "border-amber-500 bg-amber-50 text-amber-900",
  low: "border-slate-400 bg-slate-50 text-slate-800",
};

export type DriftQueueRowProps = {
  row: DriftQueueRowT;
  onDecide?: (
    row: DriftQueueRowT,
    decision: "accept" | "defer" | "reject",
  ) => void;
};

export function DriftQueueRow({ row, onDecide }: DriftQueueRowProps) {
  return (
    <div
      data-testid={`drift-queue-row-${row.drift_queue_id}`}
      className="flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-white p-2"
    >
      <div className="flex flex-col gap-0.5">
        <span className="font-mono text-[11px] text-slate-900">
          {row.person_grace_id}
        </span>
        <span className="text-[10px] text-slate-600">
          → {row.proposed_cluster_id ?? "(no proposal)"}
        </span>
        <span className="text-[10px] italic text-slate-500">
          {row.rationale}
        </span>
      </div>
      <div className="flex items-center gap-2">
        <span
          data-testid={`drift-queue-band-${row.drift_queue_id}`}
          aria-label={`Drift band: ${BAND_COPY[row.drift_band]}`}
          className={`rounded border px-2 py-0.5 text-[10px] font-medium uppercase ${BAND_CLASSES[row.drift_band]}`}
        >
          {BAND_COPY[row.drift_band]}
        </span>
        {row.status === "pending" && onDecide ? (
          <div className="flex items-center gap-1">
            <button
              type="button"
              data-testid={`drift-queue-accept-${row.drift_queue_id}`}
              onClick={() => onDecide(row, "accept")}
              className="rounded border border-emerald-500 bg-emerald-50 px-2 py-0.5 text-[10px] text-emerald-900"
            >
              Accept
            </button>
            <button
              type="button"
              data-testid={`drift-queue-defer-${row.drift_queue_id}`}
              onClick={() => onDecide(row, "defer")}
              className="rounded border border-slate-400 bg-slate-50 px-2 py-0.5 text-[10px] text-slate-800"
            >
              Defer
            </button>
            <button
              type="button"
              data-testid={`drift-queue-reject-${row.drift_queue_id}`}
              onClick={() => onDecide(row, "reject")}
              className="rounded border border-rose-500 bg-rose-50 px-2 py-0.5 text-[10px] text-rose-900"
            >
              Reject
            </button>
          </div>
        ) : row.status === "pending" && !onDecide ? (
          <span
            data-testid={`drift-queue-pending-runbook-${row.drift_queue_id}`}
            className="max-w-[14rem] text-[10px] text-slate-500"
          >
            {PERMISSIONS_COPY.driftQueueRunbookHint}
          </span>
        ) : (
          <span className="text-[10px] uppercase text-slate-500">
            {row.status}
          </span>
        )}
      </div>
    </div>
  );
}
