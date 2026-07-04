"use client";

/**
 * EvidenceBundleSummary — collapsed-by-default per-cluster evidence overlay.
 *
 * Renders a section list backed by the six-source evidence collector
 * (D332). Each section is summarized with item counts and an optional
 * confidence band string only — D120/D217 forbids numeric scoring on the
 * DOM.
 */

import { useState } from "react";
import { PERMISSIONS_COPY } from "@/lib/permissions/copy";
import type { HypothesisConfidenceBand } from "@/lib/api/types";

export type EvidenceBundleSection = {
  source_id: string;
  display_name: string;
  item_count: number;
  band?: HypothesisConfidenceBand | null;
};

export type EvidenceBundleSummaryProps = {
  evidenceId: string;
  sections: EvidenceBundleSection[];
  defaultOpen?: boolean;
};

const BAND_COPY: Record<HypothesisConfidenceBand, string> = {
  strong: PERMISSIONS_COPY.hypothesisConfidenceStrong,
  moderate: PERMISSIONS_COPY.hypothesisConfidenceModerate,
  weak: PERMISSIONS_COPY.hypothesisConfidenceWeak,
};

export function EvidenceBundleSummary({
  evidenceId,
  sections,
  defaultOpen = false,
}: EvidenceBundleSummaryProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <details
      data-testid={`evidence-bundle-summary-${evidenceId}`}
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      className="rounded-md border border-slate-200 bg-white p-2"
    >
      <summary className="cursor-pointer text-xs font-semibold text-slate-900">
        {PERMISSIONS_COPY.evidenceBundleHeading}
      </summary>
      <div className="mt-2 flex flex-col gap-1">
        {sections.length === 0 ? (
          <p className="text-[11px] italic text-slate-500">
            {PERMISSIONS_COPY.evidenceBundleEmpty}
          </p>
        ) : (
          sections.map((s) => (
            <div
              key={s.source_id}
              data-testid={`evidence-bundle-section-${evidenceId}-${s.source_id}`}
              className="flex items-center justify-between rounded border border-slate-200 px-2 py-1"
            >
              <span className="text-[11px] text-slate-800">
                {s.display_name}
              </span>
              <span className="flex items-center gap-2">
                <span className="text-[10px] text-slate-500">
                  {s.item_count} items
                </span>
                {s.band ? (
                  <span
                    className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 text-[10px] font-medium uppercase text-slate-700"
                    aria-label={`Confidence: ${BAND_COPY[s.band]}`}
                  >
                    {BAND_COPY[s.band]}
                  </span>
                ) : null}
              </span>
            </div>
          ))
        )}
      </div>
    </details>
  );
}
