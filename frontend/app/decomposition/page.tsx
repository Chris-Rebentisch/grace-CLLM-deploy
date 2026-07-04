"use client";

/**
 * /decomposition — Decomposition runs list (D328 Route 1 surface).
 *
 * Polls `GET /api/decomposition/runs` every ~3s while at least one
 * run is in the `running` status; otherwise refresh is on-demand
 * (mount + manual refresh).
 *
 * EC-12 clean: no forbidden tokens in copy. Status labels mirror the
 * backend enum verbatim.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api/client";
import type { DecompositionRunDetail } from "@/lib/api/types";

const POLL_INTERVAL_MS = 3_000;

function statusBadgeClass(status: string): string {
  switch (status) {
    case "running":
      return "border-blue-300 bg-blue-50 text-blue-900";
    case "completed":
      return "border-emerald-300 bg-emerald-50 text-emerald-900";
    case "failed":
      return "border-rose-300 bg-rose-50 text-rose-900";
    default:
      return "border-amber-300 bg-amber-50 text-amber-900";
  }
}

export default function DecompositionListPage() {
  const [runs, setRuns] = useState<DecompositionRunDetail[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const cancelledRef = useRef(false);

  const load = useCallback(async () => {
    try {
      const res = await apiClient.listDecompositionRuns({ limit: 50 });
      if (!cancelledRef.current) setRuns(res.runs ?? []);
    } catch (e) {
      if (!cancelledRef.current) {
        setErr(e instanceof Error ? e.message : "load failed");
      }
    }
  }, []);

  useEffect(() => {
    cancelledRef.current = false;
    void load();
    return () => {
      cancelledRef.current = true;
    };
  }, [load]);

  // Poll while any run is running.
  useEffect(() => {
    const anyRunning = runs.some((r) => r.status === "running");
    if (!anyRunning) return;
    const id = setInterval(() => {
      void load();
    }, POLL_INTERVAL_MS);
    return () => clearInterval(id);
  }, [runs, load]);

  return (
    <div className="p-4" data-testid="decomposition-list-page">
      <header className="mb-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">Decomposition runs</h1>
        <button
          type="button"
          data-testid="decomposition-refresh"
          onClick={() => void load()}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-xs hover:bg-slate-50"
        >
          Refresh
        </button>
      </header>
      {err ? (
        <p data-testid="decomposition-list-error" className="text-sm text-rose-600">
          {err}
        </p>
      ) : null}
      {runs.length === 0 ? (
        <p className="text-sm text-slate-500">No decomposition runs yet.</p>
      ) : (
        <ul className="space-y-2">
          {runs.map((run) => (
            <li key={run.run_id}>
              <Link
                href={`/decomposition/${encodeURIComponent(run.run_id)}`}
                className="block rounded border border-slate-200 bg-white p-2 text-sm hover:bg-slate-50"
                data-testid={`decomposition-run-row-${run.run_id}`}
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-slate-700">
                    {run.run_id.slice(0, 8)}…
                  </span>
                  <span
                    data-testid={`decomposition-status-badge-${run.run_id}`}
                    className={`rounded border px-2 py-0.5 text-[11px] ${statusBadgeClass(
                      run.status,
                    )}`}
                  >
                    {run.status}
                  </span>
                </div>
                <div className="mt-1 truncate text-xs text-slate-600">
                  {run.archive_root}
                </div>
                <div className="mt-0.5 text-[11px] text-slate-500">
                  triggered {run.triggered_at}
                  {run.completed_at ? ` · completed ${run.completed_at}` : ""}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
