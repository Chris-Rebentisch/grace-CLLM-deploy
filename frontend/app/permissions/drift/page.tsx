"use client";

/**
 * /permissions/drift — drift queue review surface (Chunk 42, D337).
 *
 * Lists `permission_drift_queue` rows. Bands surface as labels only
 * (D120/D217 — never numeric distances). Pending rows show a runbook hint
 * until a future route wires `DriftQueueRow` `onDecide`.
 */

import { useEffect, useState } from "react";
import { permissionsApi } from "@/lib/api/permissions";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";
import { DriftQueueRow } from "@/components/permissions/DriftQueueRow";
import type { DriftBand, DriftQueueRow as DriftQueueRowT } from "@/lib/api/types";

export default function PermissionsDriftPage() {
  const [rows, setRows] = useState<DriftQueueRowT[]>([]);
  const [bandFilter, setBandFilter] = useState<DriftBand | "">("");
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    permissionsApi
      .listDriftQueue(bandFilter ? { drift_band: bandFilter } : {})
      .then((resp) => {
        if (!cancelled) setRows(resp.queue);
      })
      .catch((e) => {
        if (!cancelled)
          setErr(e instanceof Error ? e.message : "Failed to load drift queue");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [bandFilter]);

  return (
    <main
      data-testid="permissions-drift-page"
      className="mx-auto flex max-w-4xl flex-col gap-3 p-4"
    >
      <h1 className="text-lg font-semibold text-slate-900">
        {PERMISSIONS_COPY.driftQueueHeading}
      </h1>

      <div className="flex items-center gap-2">
        <label className="text-xs text-slate-700">Filter by band</label>
        <select
          data-testid="permissions-drift-band-filter"
          value={bandFilter}
          onChange={(e) => setBandFilter(e.target.value as DriftBand | "")}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs"
        >
          <option value="">All bands</option>
          <option value="high">{PERMISSIONS_COPY.driftBandHigh}</option>
          <option value="medium">{PERMISSIONS_COPY.driftBandMedium}</option>
          <option value="low">{PERMISSIONS_COPY.driftBandLow}</option>
        </select>
      </div>

      {err ? (
        <p
          data-testid="permissions-drift-error"
          className="text-xs text-rose-700"
        >
          {err}
        </p>
      ) : null}

      {loading ? (
        <p className="text-xs text-slate-500">Loading…</p>
      ) : rows.length === 0 ? (
        <p
          data-testid="permissions-drift-empty"
          className="text-xs italic text-slate-500"
        >
          Queue is empty.
        </p>
      ) : (
        <div className="flex flex-col gap-1">
          {rows.map((row) => (
            <DriftQueueRow key={row.drift_queue_id} row={row} />
          ))}
        </div>
      )}
    </main>
  );
}
