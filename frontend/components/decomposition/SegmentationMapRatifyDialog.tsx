"use client";

/**
 * SegmentationMapRatifyDialog — D325.
 *
 * Confirmation dialog with a YAML preview of the proposed Segmentation
 * Map. Calls `POST /api/decomposition/runs/{run_id}/segmentation-map/ratify`
 * via {@link apiClient.ratifyDecompositionSegmentationMap} on confirm.
 *
 * The YAML preview is generated client-side from the JSON payload so we
 * never round-trip the canonical hash through the browser. The server
 * recomputes the SHA-256 canonical-JSON hash at INSERT time.
 */

import { useMemo, useState } from "react";
import { apiClient } from "@/lib/api/client";
import type { SegmentationMap } from "@/lib/api/types";

export type SegmentationMapRatifyDialogProps = {
  open: boolean;
  onClose: () => void;
  runId: string;
  segmentationMap: SegmentationMap;
  onRatified?: (result: {
    segmentation_map_id: string;
    payload_hash: string;
    previous_hash: string | null;
  }) => void;
};

/**
 * Minimal canonical YAML renderer suitable for preview only. We render
 * each top-level key and dump nested objects as JSON-in-YAML; this
 * avoids a runtime YAML dependency in the browser bundle while
 * preserving deterministic ordering for visual review.
 */
function renderYamlPreview(obj: Record<string, unknown>): string {
  const lines: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === null || v === undefined) {
      lines.push(`${k}: null`);
    } else if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
      lines.push(`${k}: ${typeof v === "string" ? JSON.stringify(v) : v}`);
    } else {
      lines.push(`${k}: ${JSON.stringify(v)}`);
    }
  }
  return lines.join("\n");
}

export function SegmentationMapRatifyDialog({
  open,
  onClose,
  runId,
  segmentationMap,
  onRatified,
}: SegmentationMapRatifyDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const yamlPreview = useMemo(
    () =>
      renderYamlPreview({
        schema_version: segmentationMap.schema_version,
        decomposition_run_id: segmentationMap.decomposition_run_id,
        null_hypothesis_accepted: segmentationMap.null_hypothesis_accepted,
        previous_hash: segmentationMap.previous_hash,
        payload: segmentationMap.payload,
      }),
    [segmentationMap],
  );

  if (!open) return null;

  const onConfirm = async () => {
    setSubmitting(true);
    setErr(null);
    try {
      const out = await apiClient.ratifyDecompositionSegmentationMap(
        runId,
        segmentationMap,
      );
      onRatified?.(out);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Ratification failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="segmentation-map-ratify-dialog"
      role="dialog"
      aria-label="Confirm segmentation map ratification"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-xl rounded-md bg-white p-4 shadow-lg">
        <h3 className="mb-2 text-base font-semibold text-slate-900">
          Ratify segmentation map
        </h3>
        <p className="mb-2 text-xs text-slate-700">
          Once ratified, this map is appended to the hash-chained Segmentation
          Map governance log for run{" "}
          <span className="font-mono">{runId.slice(0, 8)}…</span>. Append-only —
          subsequent updates require a new ratification.
        </p>
        <pre
          data-testid="segmentation-map-yaml-preview"
          className="mb-3 max-h-64 overflow-auto rounded border border-slate-200 bg-slate-50 p-2 text-[11px] leading-snug"
        >
          {yamlPreview}
        </pre>
        {err ? (
          <p
            data-testid="segmentation-map-ratify-error"
            className="mb-2 text-xs text-rose-700"
          >
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            data-testid="segmentation-map-ratify-cancel"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            type="button"
            data-testid="segmentation-map-ratify-confirm"
            onClick={onConfirm}
            disabled={submitting}
            className="rounded border border-emerald-500 bg-emerald-50 px-3 py-1 text-xs text-emerald-900 disabled:opacity-50"
          >
            {submitting ? "Ratifying…" : "Confirm ratification"}
          </button>
        </div>
      </div>
    </div>
  );
}
