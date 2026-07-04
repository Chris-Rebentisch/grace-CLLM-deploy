"use client";

/**
 * CalibrationProgressBar — progress toward minimum-review gate (Chunk 49, D394).
 *
 * D120/D217: renders a visual progress bar + label string from backend.
 * The label is pre-formatted by the backend ("N of M reviews") — the
 * component never constructs a numeric string itself.
 */

import type { TierProgress } from "@/lib/api/types";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

export type CalibrationProgressBarProps = {
  progress: TierProgress;
  testId?: string;
};

export function CalibrationProgressBar({
  progress,
  testId,
}: CalibrationProgressBarProps) {
  const pct =
    progress.min_reviews_for_calibration > 0
      ? Math.min(
          100,
          Math.round(
            (progress.total_decisions / progress.min_reviews_for_calibration) *
              100,
          ),
        )
      : 0;

  return (
    <div data-testid={testId ?? "calibration-progress"} className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-[10px] text-slate-600">
        <span>{AUTONOMY_COPY.progressGateLabel}</span>
        <span data-testid="progress-label">{progress.progress_label}</span>
      </div>
      <div className="h-2 w-full rounded-full bg-slate-200">
        <div
          className="h-2 rounded-full bg-slate-700 transition-all"
          style={{ width: `${pct}%` }}
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        />
      </div>
    </div>
  );
}
