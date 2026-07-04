"use client";

/**
 * SensitivityReportRatifyDialog — generation trigger (Chunk 43, CP6).
 *
 * Mirrors `PermissionMatrixRatifyDialog` (Chunk 42 D331 ergonomics).
 * Calls `sensitivityApi.generateReport({ force, adminKey })`. The
 * server-side route is admin-key-gated when `GRACE_ADMIN_KEY` is set;
 * loopback dev callers may submit with no key.
 *
 * D120/D217: the returned response carries band labels only — no
 * `coverage_score` float ever crosses the wire.
 */

import { useState } from "react";
import { sensitivityApi } from "@/lib/api/sensitivity";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";
import type { SensitivityClassificationReportResponse } from "@/lib/api/types";

export type SensitivityReportRatifyDialogProps = {
  open: boolean;
  onClose: () => void;
  force?: boolean;
  onGenerated?: (report: SensitivityClassificationReportResponse) => void;
};

export function SensitivityReportRatifyDialog({
  open,
  onClose,
  force = false,
  onGenerated,
}: SensitivityReportRatifyDialogProps) {
  const [adminKey, setAdminKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (!open) return null;

  const onConfirm = async () => {
    setSubmitting(true);
    setErr(null);
    try {
      const out = await sensitivityApi.generateReport({
        force,
        adminKey: adminKey.trim() === "" ? undefined : adminKey.trim(),
      });
      onGenerated?.(out);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Report generation failed");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      data-testid="sensitivity-report-ratify-dialog"
      role="dialog"
      aria-label={SENSITIVITY_COPY.reportGenerateCta}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    >
      <div className="w-full max-w-lg rounded-md bg-white p-4 shadow-lg">
        <h3 className="mb-2 text-base font-semibold text-slate-900">
          {force
            ? SENSITIVITY_COPY.reportRegenerateCta
            : SENSITIVITY_COPY.reportGenerateCta}
        </h3>
        <p className="mb-3 text-xs text-slate-700">
          {SENSITIVITY_COPY.reportRatifyDescription}
        </p>
        <label
          className="mb-3 flex flex-col gap-1 text-[11px] text-slate-600"
          htmlFor="sensitivity-report-admin-key"
        >
          <span>Admin key (optional on loopback)</span>
          <input
            id="sensitivity-report-admin-key"
            data-testid="sensitivity-report-admin-key"
            type="password"
            value={adminKey}
            onChange={(e) => setAdminKey(e.target.value)}
            disabled={submitting}
            autoComplete="off"
            className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-900 disabled:opacity-50"
          />
        </label>
        {err ? (
          <p
            data-testid="sensitivity-report-ratify-error"
            className="mb-2 text-xs text-rose-700"
          >
            {err}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <button
            type="button"
            data-testid="sensitivity-report-ratify-cancel"
            onClick={onClose}
            disabled={submitting}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs text-slate-700 disabled:opacity-50"
          >
            {SENSITIVITY_COPY.reportRatifyCancel}
          </button>
          <button
            type="button"
            data-testid="sensitivity-report-ratify-confirm"
            onClick={onConfirm}
            disabled={submitting}
            className="rounded border border-emerald-500 bg-emerald-50 px-3 py-1 text-xs text-emerald-900 disabled:opacity-50"
          >
            {submitting ? "Generating…" : SENSITIVITY_COPY.reportRatifyConfirm}
          </button>
        </div>
      </div>
    </div>
  );
}
