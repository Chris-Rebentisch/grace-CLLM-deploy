"use client";
import { useState } from "react";
import type { ConfigureSourcesResponse } from "@/lib/api/types";
import { useConfigureSources } from "@/lib/query/sources";
import { useSessionStore } from "@/lib/state/session-store";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { FileBrowser } from "./FileBrowser";
import { SourcesConfirmModal } from "./SourcesConfirmModal";
import { ProcessingPanel } from "./ProcessingPanel";

function basename(p: string): string {
  const parts = p.split("/").filter(Boolean);
  return parts[parts.length - 1] ?? p;
}

export function SourceSelector() {
  const configureMut = useConfigureSources();
  const sessionId = useSessionStore((s) => s.sessionId);
  const phase = useSessionStore((s) => s.activePhase);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<ConfigureSourcesResponse | null>(null);
  const [configured, setConfigured] = useState<ConfigureSourcesResponse | null>(null);

  const toggle = (path: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const handleSubmit = async () => {
    const result = await configureMut.mutateAsync({
      selected_paths: Array.from(selected),
    });
    setPreview(result);
  };

  const handleConfirm = () => {
    if (!preview) return;
    emitTelemetry("sources_configured", {
      file_count: preview.total_files,
      // backend does not surface byte total in configure response; expose 0
      // so the schema field stays present (D215 hashed-payload posture).
      total_size_mb: 0,
      estimated_processing_minutes: preview.estimated_processing_minutes,
    });
    setConfigured(preview);
    setPreview(null);
    void sessionId;
    void phase;
  };

  const selectedList = Array.from(selected);

  return (
    <div className="space-y-3 p-4" data-testid="source-selector">
      <h1 className="text-lg font-semibold">Configure sources</h1>
      <p className="text-xs text-slate-500">
        Browse your computer and check the exact folders or files to include.
        Folders are scanned recursively for supported document types.
      </p>

      <FileBrowser selected={selected} onToggle={toggle} />

      {/* Cross-folder selection summary — selections persist as you navigate. */}
      {selectedList.length > 0 && (
        <div
          data-testid="selected-summary"
          className="rounded border bg-slate-50 p-2 text-xs"
        >
          <div className="mb-1 flex items-center justify-between">
            <span className="font-medium">Selected ({selectedList.length})</span>
            <button
              type="button"
              data-testid="clear-selection"
              onClick={() => setSelected(new Set())}
              className="text-slate-500 underline"
            >
              Clear all
            </button>
          </div>
          <ul className="max-h-32 space-y-0.5 overflow-auto">
            {selectedList.map((p) => (
              <li key={p} className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => toggle(p)}
                  className="text-rose-500"
                  title="Remove"
                  data-testid={`remove-${basename(p)}`}
                >
                  ✕
                </button>
                <span className="truncate font-mono text-slate-600" title={p}>
                  {p}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <button
        type="button"
        data-testid="sources-submit"
        disabled={selected.size === 0 || configureMut.isPending}
        onClick={() => void handleSubmit()}
        className="rounded bg-slate-800 px-3 py-1 text-xs text-white disabled:opacity-50"
      >
        Continue ({selected.size} selected)
      </button>

      {configured && (
        <ProcessingPanel
          manifestPath={configured.manifest_path}
          totalFiles={configured.total_files}
        />
      )}

      <SourcesConfirmModal
        preview={preview}
        onCancel={() => setPreview(null)}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
