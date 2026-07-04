"use client";

import { useState } from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Badge } from "@/components/ui/badge";
import { ScopeDropdown } from "./ScopeDropdown";
import { useScopeStore } from "@/lib/state/scope-store";

// D229 interactive scope dropdown host. Promoted from Chunk 27 D194
// read-only chip.
export function ScopeIndicator() {
  const [open, setOpen] = useState(false);
  const { isAllSegments, selectedSegments } = useScopeStore();

  const label = isAllSegments
    ? "Scope: All"
    : `Scope: ${selectedSegments.length} segment${selectedSegments.length !== 1 ? "s" : ""}`;

  return (
    <div className="relative">
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            data-testid="scope-indicator"
            aria-label={`Graph scope: ${isAllSegments ? "all" : selectedSegments.join(", ")}`}
            className="rounded-md border border-border bg-background px-1.5 py-0.5 text-xs"
            onClick={() => setOpen(!open)}
          >
            <Badge variant="outline" className="font-normal">
              {label}
            </Badge>
          </button>
        </TooltipTrigger>
        <TooltipContent
          data-testid="scope-indicator-tooltip"
          className="max-w-xs text-xs"
        >
          Filter all read paths and CQ scope to selected segments. Cleared on
          session end.
        </TooltipContent>
      </Tooltip>
      {open && <ScopeDropdown />}
    </div>
  );
}
