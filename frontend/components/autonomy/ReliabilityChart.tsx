"use client";

/**
 * ReliabilityChart — per-tier div-based bar chart of calibration bands
 * (Chunk 49, D394).
 *
 * D120/D217: approval rate numerics MUST NOT reach the DOM. Each band
 * bar uses a CSS width percentage for the visual, but the text label is
 * a three-level band label ("Consistently approved" / "Mixed outcomes" /
 * "Frequently revised") derived from the approval rate range. No chart
 * library — pure divs + Tailwind.
 */

import type { CalibrationBand } from "@/lib/api/types";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

function approvalBandLabel(rate: number): string {
  if (rate >= 0.8) return AUTONOMY_COPY.bandApprovalHigh;
  if (rate >= 0.5) return AUTONOMY_COPY.bandApprovalMedium;
  return AUTONOMY_COPY.bandApprovalLow;
}

function approvalBarColor(rate: number): string {
  if (rate >= 0.8) return "bg-emerald-500";
  if (rate >= 0.5) return "bg-amber-500";
  return "bg-rose-500";
}

function bandRangeLabel(band: CalibrationBand): string {
  const lo = Math.round(band.band_low * 100);
  const hi = Math.round(band.band_high * 100);
  return `${lo}–${hi}%`;
}

export type ReliabilityChartProps = {
  bands: CalibrationBand[];
  testId?: string;
};

export function ReliabilityChart({ bands, testId }: ReliabilityChartProps) {
  if (bands.length === 0) {
    return (
      <p
        data-testid={testId ? `${testId}-empty` : "reliability-chart-empty"}
        className="text-xs italic text-slate-500"
      >
        {AUTONOMY_COPY.reliabilityEmpty}
      </p>
    );
  }

  return (
    <div
      data-testid={testId ?? "reliability-chart"}
      className="flex flex-col gap-1.5"
    >
      {bands.map((band, idx) => {
        const widthPct = Math.max(4, Math.round(band.approval_rate * 100));
        return (
          <div
            key={idx}
            data-testid={`reliability-band-${idx}`}
            className="flex items-center gap-2"
          >
            <span
              className="w-16 shrink-0 text-right text-[10px] text-slate-600"
              data-testid={`band-range-${idx}`}
            >
              {bandRangeLabel(band)}
            </span>
            <div className="flex-1">
              <div className="h-4 w-full rounded bg-slate-100">
                <div
                  className={`h-4 rounded ${approvalBarColor(band.approval_rate)}`}
                  style={{ width: `${widthPct}%` }}
                />
              </div>
            </div>
            <span
              className="w-32 shrink-0 text-[10px] text-slate-700"
              data-testid={`band-label-${idx}`}
            >
              {approvalBandLabel(band.approval_rate)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
