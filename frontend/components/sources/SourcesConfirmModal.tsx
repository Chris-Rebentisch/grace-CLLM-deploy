"use client";
import type { ConfigureSourcesResponse } from "@/lib/api/types";

export function SourcesConfirmModal({
  preview,
  onConfirm,
  onCancel,
}: {
  preview: ConfigureSourcesResponse | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (!preview) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      data-testid="sources-confirm-modal"
      className="fixed inset-0 flex items-center justify-center bg-black/40"
    >
      <div className="w-96 rounded bg-white p-4 text-sm">
        <h3 className="text-base font-semibold">Confirm source set</h3>
        <dl className="mt-2 grid grid-cols-2 gap-y-1 text-xs">
          <dt>Files selected</dt>
          <dd data-testid="confirm-file-count">{preview.total_files}</dd>
          <dt>Estimated processing</dt>
          <dd data-testid="confirm-processing-minutes">
            {preview.estimated_processing_minutes} min
          </dd>
          <dt>Manifest</dt>
          <dd data-testid="confirm-manifest-path" className="truncate">
            {preview.manifest_path}
          </dd>
        </dl>
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            data-testid="confirm-cancel"
            className="rounded border px-2 py-1 text-xs"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            data-testid="confirm-accept"
            className="rounded bg-emerald-700 px-2 py-1 text-xs text-white"
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
}
