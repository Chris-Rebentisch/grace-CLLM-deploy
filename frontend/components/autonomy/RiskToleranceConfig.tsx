"use client";

/**
 * RiskToleranceConfig — per-tier dropdown config for risk tolerance,
 * window size, and min reviews (Chunk 49, D394–D397).
 *
 * Sends PATCH /api/ontology/calibration/config/{tier}. Mutating —
 * admin-key header required when GRACE_ADMIN_KEY is set.
 */

import { useState } from "react";
import { apiRequest } from "@/lib/api/client";
import type { TrustScoreState } from "@/lib/api/types";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

const RISK_TOLERANCE_OPTIONS = [
  { value: 0.80, label: "80%" },
  { value: 0.85, label: "85%" },
  { value: 0.90, label: "90%" },
  { value: 0.95, label: "95%" },
  { value: 0.99, label: "99%" },
];

const WINDOW_SIZE_OPTIONS = [
  { value: 20, label: "20" },
  { value: 30, label: "30" },
  { value: 50, label: "50" },
  { value: 100, label: "100" },
  { value: 200, label: "200" },
];

const MIN_REVIEWS_OPTIONS = [
  { value: 10, label: "10" },
  { value: 25, label: "25" },
  { value: 50, label: "50" },
  { value: 100, label: "100" },
  { value: 200, label: "200" },
];

export type RiskToleranceConfigProps = {
  tier: number;
  trustState: TrustScoreState;
  onUpdated?: (updated: TrustScoreState) => void;
  testId?: string;
};

export function RiskToleranceConfig({
  tier,
  trustState,
  onUpdated,
  testId,
}: RiskToleranceConfigProps) {
  const [riskTolerance, setRiskTolerance] = useState(trustState.risk_tolerance);
  const [windowSize, setWindowSize] = useState(trustState.window_size);
  const [minReviews, setMinReviews] = useState(
    trustState.min_reviews_for_calibration,
  );
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const hasChanges =
    riskTolerance !== trustState.risk_tolerance ||
    windowSize !== trustState.window_size ||
    minReviews !== trustState.min_reviews_for_calibration;

  const handleSave = async () => {
    setSaving(true);
    setMsg(null);
    setErr(null);
    try {
      const body: Record<string, number> = {};
      if (riskTolerance !== trustState.risk_tolerance)
        body.risk_tolerance = riskTolerance;
      if (windowSize !== trustState.window_size)
        body.window_size = windowSize;
      if (minReviews !== trustState.min_reviews_for_calibration)
        body.min_reviews_for_calibration = minReviews;

      const updated = await apiRequest<TrustScoreState>(
        `/api/ontology/calibration/config/${tier}`,
        { method: "PATCH", body },
      );
      setMsg(AUTONOMY_COPY.configSaved);
      onUpdated?.(updated);
    } catch (e) {
      setErr(e instanceof Error ? e.message : AUTONOMY_COPY.configError);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      data-testid={testId ?? `risk-tolerance-config-${tier}`}
      className="flex flex-col gap-2 rounded border border-slate-200 bg-white p-3"
    >
      <h4 className="text-[11px] font-semibold text-slate-800">
        {AUTONOMY_COPY.riskToleranceHeading}
      </h4>

      <label className="flex items-center gap-2 text-[10px] text-slate-600">
        <span className="w-40 shrink-0">{AUTONOMY_COPY.riskToleranceLabel}</span>
        <select
          data-testid="risk-tolerance-select"
          value={riskTolerance}
          onChange={(e) => setRiskTolerance(parseFloat(e.target.value))}
          className="rounded border border-slate-300 px-1 py-0.5 text-[10px]"
        >
          {RISK_TOLERANCE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-[10px] text-slate-600">
        <span className="w-40 shrink-0">{AUTONOMY_COPY.windowSizeLabel}</span>
        <select
          data-testid="window-size-select"
          value={windowSize}
          onChange={(e) => setWindowSize(parseInt(e.target.value, 10))}
          className="rounded border border-slate-300 px-1 py-0.5 text-[10px]"
        >
          {WINDOW_SIZE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>

      <label className="flex items-center gap-2 text-[10px] text-slate-600">
        <span className="w-40 shrink-0">{AUTONOMY_COPY.minReviewsLabel}</span>
        <select
          data-testid="min-reviews-select"
          value={minReviews}
          onChange={(e) => setMinReviews(parseInt(e.target.value, 10))}
          className="rounded border border-slate-300 px-1 py-0.5 text-[10px]"
        >
          {MIN_REVIEWS_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </label>

      <div className="flex items-center gap-2">
        <button
          type="button"
          data-testid="save-config-button"
          disabled={!hasChanges || saving}
          onClick={handleSave}
          className="rounded border border-slate-700 bg-slate-800 px-3 py-1 text-[10px] font-medium text-white disabled:opacity-50"
        >
          {saving ? "Saving\u2026" : "Save"}
        </button>
        {msg ? (
          <span data-testid="config-success-msg" className="text-[10px] text-emerald-700">
            {msg}
          </span>
        ) : null}
        {err ? (
          <span data-testid="config-error-msg" className="text-[10px] text-rose-700">
            {err}
          </span>
        ) : null}
      </div>
    </div>
  );
}
