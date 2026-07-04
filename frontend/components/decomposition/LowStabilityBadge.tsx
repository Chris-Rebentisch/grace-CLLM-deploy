"use client";

/**
 * LowStabilityBadge — D322 / EC-12 mirror.
 *
 * Auto-promoted at the top of the run detail page when
 * `low_stability_flag === true`. The copy is deliberately worded as a
 * forward-looking recommendation, never as a deficit / error / drift /
 * reality-gap statement (EC-12 forbidden tokens: drift, reality gap,
 * mistake, incorrect, failure, deficit, blind spot, wrong).
 */

export type LowStabilityBadgeProps = {
  visible: boolean;
  onRerunRecommended?: () => void;
};

export function LowStabilityBadge({
  visible,
  onRerunRecommended,
}: LowStabilityBadgeProps) {
  if (!visible) return null;
  return (
    <div
      data-testid="low-stability-badge"
      role="status"
      aria-live="polite"
      className="mb-3 flex items-center justify-between rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs"
    >
      <span className="font-medium text-amber-900">
        Re-run recommended — Layer 3 stability score below threshold
      </span>
      {onRerunRecommended ? (
        <button
          type="button"
          onClick={onRerunRecommended}
          data-testid="low-stability-badge-rerun"
          className="ml-2 rounded border border-amber-400 bg-white px-2 py-0.5 text-amber-900 hover:bg-amber-100"
        >
          Re-run with finer resolution
        </button>
      ) : null}
    </div>
  );
}
