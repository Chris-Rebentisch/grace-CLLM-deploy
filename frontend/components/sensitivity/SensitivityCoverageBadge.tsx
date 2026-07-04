"use client";

/**
 * SensitivityCoverageBadge — three-band gauge label (D344).
 *
 * D120/D217: bands only — never the underlying `coverage_score` float.
 * Below-floor (no tags anywhere on the matrix) renders a fourth
 * "Below tag floor" label distinct from high/medium/low.
 */

import type { SensitivityCoverageBand } from "@/lib/api/types";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";

const BAND_COPY: Record<SensitivityCoverageBand, string> = {
  high: SENSITIVITY_COPY.coverageBandHigh,
  medium: SENSITIVITY_COPY.coverageBandMedium,
  low: SENSITIVITY_COPY.coverageBandLow,
};

const BAND_CLASSES: Record<SensitivityCoverageBand, string> = {
  high: "border-emerald-500 bg-emerald-50 text-emerald-900",
  medium: "border-amber-500 bg-amber-50 text-amber-900",
  low: "border-rose-500 bg-rose-50 text-rose-900",
};

export type SensitivityCoverageBadgeProps = {
  band: SensitivityCoverageBand | null;
  testId?: string;
};

export function SensitivityCoverageBadge({
  band,
  testId,
}: SensitivityCoverageBadgeProps) {
  if (band === null) {
    return (
      <span
        data-testid={testId ?? "sensitivity-coverage-badge-below-floor"}
        aria-label={SENSITIVITY_COPY.coverageBandUnknown}
        className="rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-[10px] font-medium uppercase text-slate-700"
      >
        {SENSITIVITY_COPY.coverageBandUnknown}
      </span>
    );
  }
  return (
    <span
      data-testid={testId ?? `sensitivity-coverage-badge-${band}`}
      aria-label={BAND_COPY[band]}
      className={`rounded border px-2 py-0.5 text-[10px] font-medium uppercase ${BAND_CLASSES[band]}`}
    >
      {BAND_COPY[band]}
    </span>
  );
}
