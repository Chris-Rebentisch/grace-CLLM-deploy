"use client";

// D297 (Chunk 38) — Reconciliation Bridge "change-in-flight" framing.
// Renders a banner at the top of recon viewers when one or more
// active Change Directives cover the current segment.

import {
  COVERING_DIRECTIVES_HEADING,
  COVERING_DIRECTIVES_SUBTITLE,
} from "@/lib/recon/report_copy";
import type { CoveringDirective } from "@/lib/api/types";

export type CoveringDirectivesBannerProps = {
  directives: CoveringDirective[];
};

export function CoveringDirectivesBanner({
  directives,
}: CoveringDirectivesBannerProps) {
  if (!directives || directives.length === 0) return null;
  return (
    <section
      data-testid="covering-directives-banner"
      className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
    >
      <h3 className="mb-1 font-semibold">{COVERING_DIRECTIVES_HEADING}</h3>
      <p className="mb-2 text-xs">{COVERING_DIRECTIVES_SUBTITLE}</p>
      <ul className="ml-4 list-disc">
        {directives.map((d) => (
          <li
            key={d.directive_id}
            data-testid={`covering-directive-${d.directive_id}`}
          >
            <span className="font-medium">{d.title}</span>
            <span className="ml-2 text-xs text-amber-800">[{d.tier}]</span>
            {d.progress_percentage != null && d.velocity_band ? (
              <span className="ml-2 text-xs text-amber-900">
                · realization band: {d.velocity_band}
                {d.is_stalled ? " (stalled)" : ""}
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}
