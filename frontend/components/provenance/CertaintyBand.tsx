"use client";

import type { CertaintyBand } from "@/lib/api/types";
import { cn } from "@/lib/utils";

// D191 band vocabulary. Pydantic values are lowercase; UI display is
// capitalized. D120: never show numeric scores.
export const CERTAINTY_BAND_DISPLAY: Record<CertaintyBand, string> = {
  high: "High",
  medium: "Medium",
  low: "Low",
  insufficient_evidence: "Insufficient Evidence",
};

export const CERTAINTY_BAND_DESCRIPTION: Record<CertaintyBand, string> = {
  high: "Strong graph evidence supports this claim.",
  medium: "Some evidence supports this claim.",
  low: "Limited evidence. Treat with care.",
  insufficient_evidence:
    "Not enough evidence in the graph. Additional data required.",
};

// D191 visual marker mapping.
//   high                  → solid underline + muted filled tone
//   medium                → outlined dotted underline
//   low                   → dashed underline
//   insufficient_evidence → neutral marker + "needs more data" affordance
export const CERTAINTY_BAND_MARKER_CLASS: Record<CertaintyBand, string> = {
  high: "underline decoration-solid decoration-2 decoration-emerald-500/70 underline-offset-4",
  medium:
    "underline decoration-dotted decoration-2 decoration-amber-500/80 underline-offset-4",
  low: "underline decoration-dashed decoration-2 decoration-orange-500/80 underline-offset-4",
  insufficient_evidence:
    "underline decoration-wavy decoration-2 decoration-muted-foreground/70 underline-offset-4",
};

export function bandMarkerClass(band: CertaintyBand): string {
  return CERTAINTY_BAND_MARKER_CLASS[band];
}

export function BandLabel({
  band,
  className,
}: {
  band: CertaintyBand;
  className?: string;
}) {
  return (
    <span
      data-testid={`band-label-${band}`}
      data-band={band}
      className={cn("text-xs font-medium", className)}
    >
      {CERTAINTY_BAND_DISPLAY[band]}
    </span>
  );
}
