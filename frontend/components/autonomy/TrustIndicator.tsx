"use client";

/**
 * TrustIndicator — three-band trust readiness badge (Chunk 49, D394).
 *
 * D120/D217: trust indicator surfaces as label string only — never the
 * underlying `trust_score` float.
 */

import type { TrustIndicator as TrustIndicatorType } from "@/lib/api/types";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

const INDICATOR_COPY: Record<TrustIndicatorType, string> = {
  high: AUTONOMY_COPY.trustIndicatorHigh,
  building: AUTONOMY_COPY.trustIndicatorBuilding,
  insufficient: AUTONOMY_COPY.trustIndicatorInsufficient,
};

const INDICATOR_CLASSES: Record<TrustIndicatorType, string> = {
  high: "border-emerald-500 bg-emerald-50 text-emerald-900",
  building: "border-amber-500 bg-amber-50 text-amber-900",
  insufficient: "border-slate-300 bg-slate-50 text-slate-700",
};

export type TrustIndicatorProps = {
  indicator: TrustIndicatorType;
  testId?: string;
};

export function TrustIndicatorBadge({
  indicator,
  testId,
}: TrustIndicatorProps) {
  return (
    <span
      data-testid={testId ?? `trust-indicator-${indicator}`}
      aria-label={INDICATOR_COPY[indicator]}
      className={`rounded border px-2 py-0.5 text-[10px] font-medium uppercase ${INDICATOR_CLASSES[indicator]}`}
    >
      {INDICATOR_COPY[indicator]}
    </span>
  );
}
