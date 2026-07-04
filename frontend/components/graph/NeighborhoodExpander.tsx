"use client";

import { useState } from "react";
import { useNeighborhood } from "@/lib/query/graph";

export type NeighborhoodExpanderProps = {
  graceId: string | null;
};

export function NeighborhoodExpander({ graceId }: NeighborhoodExpanderProps) {
  const [depth, setDepth] = useState<1 | 2 | null>(null);
  const query = useNeighborhood(graceId, depth ?? 1, {
    enabled: !!graceId && depth !== null,
  });

  if (!graceId) {
    return (
      <div
        data-testid="neighborhood-expander-disabled"
        className="text-xs text-slate-400 px-3 py-2"
      >
        Select a node to expand its neighborhood.
      </div>
    );
  }

  return (
    <div
      data-testid="neighborhood-expander"
      className="px-3 py-2 border-t bg-white flex items-center gap-2"
    >
      <span className="text-xs text-slate-600">Expand:</span>
      <button
        type="button"
        data-testid="expand-depth-1"
        onClick={() => setDepth(1)}
        className={
          depth === 1
            ? "text-xs rounded-md bg-slate-800 text-white px-2 py-1"
            : "text-xs rounded-md border border-slate-300 bg-white text-slate-700 px-2 py-1"
        }
      >
        Depth 1
      </button>
      <button
        type="button"
        data-testid="expand-depth-2"
        onClick={() => setDepth(2)}
        className={
          depth === 2
            ? "text-xs rounded-md bg-slate-800 text-white px-2 py-1"
            : "text-xs rounded-md border border-slate-300 bg-white text-slate-700 px-2 py-1"
        }
      >
        Depth 2
      </button>
      {query.isLoading && (
        <span className="text-xs text-slate-500" data-testid="expander-loading">
          Loading…
        </span>
      )}
      {query.isSuccess && query.data && (
        <span
          className="text-xs text-slate-600"
          data-testid="expander-summary"
        >
          {(query.data.neighbors ?? []).length} neighbors,{" "}
          {(query.data.edges ?? []).length} edges
        </span>
      )}
    </div>
  );
}
