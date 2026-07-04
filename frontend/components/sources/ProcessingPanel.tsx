"use client";
import { useState } from "react";
import { useProcessDocuments, useProcessingStatus } from "@/lib/query/sources";

/**
 * Launches document processing (POST /api/discovery/process) for the configured
 * manifest and shows live progress by polling GET /api/discovery/status. This is
 * the UI control that was previously missing — the Sources flow used to dead-end
 * at the confirm modal with no way to start ingestion.
 */
export function ProcessingPanel({
  manifestPath,
  totalFiles,
}: {
  manifestPath: string;
  totalFiles: number;
}) {
  const [started, setStarted] = useState(false);
  const processMut = useProcessDocuments();
  const { data: status } = useProcessingStatus(started);

  const byStatus = status?.by_status ?? {};
  const processed = Object.values(byStatus).reduce((a, b) => a + b, 0);
  const pct = totalFiles > 0 ? Math.min(100, Math.round((processed / totalFiles) * 100)) : 0;
  const done = started && totalFiles > 0 && processed >= totalFiles;

  const start = async () => {
    await processMut.mutateAsync(manifestPath || undefined);
    setStarted(true);
  };

  return (
    <div
      data-testid="processing-panel"
      className="space-y-2 rounded border bg-white p-3 text-xs"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Process documents</h2>
        <button
          type="button"
          data-testid="start-processing"
          disabled={started || processMut.isPending}
          onClick={() => void start()}
          className="rounded bg-emerald-700 px-3 py-1 text-white disabled:opacity-50"
        >
          {processMut.isPending
            ? "Starting…"
            : started
              ? "Processing started"
              : `Start processing (${totalFiles} files)`}
        </button>
      </div>

      {processMut.isError && (
        <p className="text-rose-600">Failed to start processing. Is the API running?</p>
      )}

      {started && (
        <div data-testid="processing-progress" className="space-y-1">
          <div className="h-2 w-full overflow-hidden rounded bg-slate-100">
            <div
              className="h-full bg-emerald-600 transition-all"
              style={{ width: `${pct}%` }}
            />
          </div>
          <p className="text-slate-600">
            {processed} / {totalFiles} documents processed
            {done ? " — complete" : " — converting + chunking…"}
          </p>
          {Object.keys(byStatus).length > 0 && (
            <ul className="flex flex-wrap gap-2 text-slate-500">
              {Object.entries(byStatus).map(([s, n]) => (
                <li key={s} data-testid={`status-${s}`}>
                  {s}: {n}
                </li>
              ))}
            </ul>
          )}
          {done && (
            <a
              href="/onboarding"
              data-testid="processing-to-review"
              className="inline-block rounded bg-slate-800 px-3 py-1 text-white"
            >
              Continue to ontology proposal →
            </a>
          )}
        </div>
      )}
    </div>
  );
}
