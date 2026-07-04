"use client";

/**
 * PermissionMatrixRatifyDialog — clones D325 SegmentationMapRatifyDialog.
 *
 * YAML preview + confirm dialog for the ratification flow. Calls
 * `permissionsApi.ratifyMatrix` on confirm. The server recomputes the
 * SHA-256 canonical-JSON `payload_hash` at INSERT (D331 — client-supplied
 * hashes are ignored).
 */

import { useMemo, useState } from "react";
import { permissionsApi } from "@/lib/api/permissions";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";
import type { PermissionMatrixVersion } from "@/lib/api/types";

export type PermissionMatrixRatifyDialogProps = {
  open: boolean;
  onClose: () => void;
  matrix: Record<string, unknown>;
  versionLabel?: string | null;
  createdBy?: string | null;
  onRatified?: (version: PermissionMatrixVersion) => void;
};

function renderYamlPreview(obj: Record<string, unknown>): string {
  const lines: string[] = [];
  for (const [k, v] of Object.entries(obj)) {
    if (v === null || v === undefined) {
      lines.push(`${k}: null`);
    } else if (
      typeof v === "string" ||
      typeof v === "number" ||
      typeof v === "boolean"
    ) {
      lines.push(`${k}: ${typeof v === "string" ? JSON.stringify(v) : v}`);
    } else {
      lines.push(`${k}: ${JSON.stringify(v)}`);
    }
  }
  return lines.join("\n");
}

export function PermissionMatrixRatifyDialog({
  open,
  onClose,
  matrix,
  versionLabel = null,
  createdBy = null,
  onRatified,
}: PermissionMatrixRatifyDialogProps) {
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const yamlPreview = useMemo(() => renderYamlPreview(matrix), [matrix]);

  if (!open) return null;

  const onConfirm = async () => {
    setSubmitting(true);
    setErr(null);
    try {
      const out = await permissionsApi.ratifyMatrix({
        matrix,
        created_by: createdBy,
        version_label: versionLabel,
      });
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
      data-testid="permission-matrix-ratify-dialog"
      role="dialog"
      aria-label={PERMISSIONS_COPY.ratifyHeading}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-xl rounded-md bg-white p-4 shadow-lg">
        <h3 className="mb-2 text-base font-semibold text-slate-900">
          {PERMISSIONS_COPY.ratifyHeading}
        </h3>
        <p className="mb-2 text-xs text-slate-700">
          {PERMISSIONS_COPY.ratifyDescription}
        </p>
        <pre
          data-testid="permission-matrix-yaml-preview"
          className="mb-3 max-h-64 overflow-auto rounded border border-slate-200 bg-slate-50 p-2 text-[11px] leading-snug"
        >
          {yamlPreview}
        </pre>
        {err ? (
          <p
            data-testid="permission-matrix-ratify-error"
            className="mb-2 text-xs text-rose-700"
          >
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            data-testid="permission-matrix-ratify-cancel"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 disabled:opacity-50"
          >
            {PERMISSIONS_COPY.ratifyCancel}
          </button>
          <button
            type="button"
            data-testid="permission-matrix-ratify-confirm"
            onClick={onConfirm}
            disabled={submitting}
            className="rounded border border-emerald-500 bg-emerald-50 px-3 py-1 text-xs text-emerald-900 disabled:opacity-50"
          >
            {submitting ? "Ratifying…" : PERMISSIONS_COPY.ratifyConfirm}
          </button>
        </div>
      </div>
    </div>
  );
}
