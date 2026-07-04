"use client";

import type { ReactNode } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type { ClaimSpan } from "@/lib/api/types";
import {
  CERTAINTY_BAND_DESCRIPTION,
  CERTAINTY_BAND_DISPLAY,
  bandMarkerClass,
} from "./CertaintyBand";

export type CertaintyChipProps = {
  span: ClaimSpan;
  children: ReactNode;
};

// Hover/click opens the tooltip. D120: this component MUST NOT render any
// numeric confidence score. Only band label + evidence ids + description.
export function CertaintyChip({ span, children }: CertaintyChipProps) {
  const needsMoreData = span.certainty_band === "insufficient_evidence";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid={`claim-span-trigger-${span.certainty_band}`}
          data-band={span.certainty_band}
          data-span-confidence={span.span_confidence}
          tabIndex={0}
          role="button"
          aria-label={`${CERTAINTY_BAND_DISPLAY[span.certainty_band]} certainty span: ${span.text}`}
          className={`cursor-help rounded-sm px-0.5 ${bandMarkerClass(span.certainty_band)}`}
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent
        data-testid="certainty-chip-popover"
        className="max-w-sm space-y-2 text-xs"
      >
        <p className="font-medium" data-band={span.certainty_band}>
          {CERTAINTY_BAND_DISPLAY[span.certainty_band]}
        </p>
        <p className="text-muted-foreground">
          {CERTAINTY_BAND_DESCRIPTION[span.certainty_band]}
        </p>
        {span.supporting_grace_ids.length > 0 ? (
          <div>
            <p className="text-muted-foreground">
              {span.supporting_grace_ids.length} supporting{" "}
              {span.supporting_grace_ids.length === 1 ? "entity" : "entities"}
            </p>
            <ul className="mt-1 flex flex-wrap gap-1">
              {span.supporting_grace_ids.slice(0, 6).map((id) => (
                <li
                  key={id}
                  className="rounded border border-border bg-muted/50 px-1 py-0.5 font-mono"
                >
                  {id.slice(0, 8)}
                </li>
              ))}
              {span.supporting_grace_ids.length > 6 ? (
                <li className="text-muted-foreground">
                  +{span.supporting_grace_ids.length - 6} more
                </li>
              ) : null}
            </ul>
          </div>
        ) : null}
        {needsMoreData ? (
          <p
            data-testid="needs-more-data"
            className="rounded bg-muted/50 px-2 py-1 text-[11px] text-muted-foreground"
          >
            Needs more data in the graph.
          </p>
        ) : null}
        <p className="text-muted-foreground">
          Full retrieval inspector arrives in Chunk 28.
        </p>
      </TooltipContent>
    </Tooltip>
  );
}
