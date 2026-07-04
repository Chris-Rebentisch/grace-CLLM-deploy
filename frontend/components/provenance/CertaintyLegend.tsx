"use client";

import { useEffect } from "react";
import type { CertaintyBand } from "@/lib/api/types";
import { useSessionStore } from "@/lib/state/session-store";
import {
  CERTAINTY_BAND_DESCRIPTION,
  CERTAINTY_BAND_DISPLAY,
  bandMarkerClass,
} from "./CertaintyBand";

const BANDS: CertaintyBand[] = ["high", "medium", "low", "insufficient_evidence"];

// D199 session-level legend. Rendered once per session. Default: expanded
// on first Open entry, collapsed thereafter. Collapse state is tracked in
// session-store so it persists across remounts inside a single session.
export function CertaintyLegend() {
  const activePhase = useSessionStore((s) => s.activePhase);
  const collapsed = useSessionStore((s) => s.legendCollapsedThisSession);
  const setLegendCollapsed = useSessionStore((s) => s.setLegendCollapsed);
  const sessionStatus = useSessionStore((s) => s.sessionStatus);

  // On first Open entry of the session, expand once; never auto-collapse.
  useEffect(() => {
    if (activePhase === "open" && sessionStatus === "active") {
      // No-op when default (collapsed=false). We intentionally do not
      // re-open later messages to avoid the "redundant explanation"
      // anti-pattern.
    }
  }, [activePhase, sessionStatus]);

  if (sessionStatus === "idle") return null;

  return (
    <aside
      data-testid="certainty-legend"
      data-collapsed={collapsed}
      aria-label="Certainty band legend"
      className="mx-4 my-2 rounded-md border border-border/60 bg-muted/30 px-3 py-2 text-xs"
    >
      <button
        type="button"
        data-testid="legend-toggle"
        className="flex w-full items-center justify-between font-medium"
        aria-expanded={!collapsed}
        onClick={() => setLegendCollapsed(!collapsed)}
      >
        Certainty band legend
        <span aria-hidden="true">{collapsed ? "+" : "−"}</span>
      </button>
      {collapsed ? null : (
        <ul
          className="mt-2 flex flex-col gap-1.5"
          data-testid="legend-list"
        >
          {BANDS.map((band) => (
            <li key={band} className="flex items-start gap-2">
              <span className={`${bandMarkerClass(band)} px-1`}>sample</span>
              <span className="flex-1">
                <span className="font-medium" data-band={band}>
                  {CERTAINTY_BAND_DISPLAY[band]}
                </span>
                <span className="ml-2 text-muted-foreground">
                  {CERTAINTY_BAND_DESCRIPTION[band]}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </aside>
  );
}
