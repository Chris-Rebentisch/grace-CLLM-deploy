"use client";

import { CQCard, type CoverageBand } from "./CQCard";

export type CQCanvasItem = {
  cqId: string;
  cqText: string;
  cqType: string;
  domain: string;
  coverageBand: CoverageBand;
  dependentTypes: string[];
};

export type CQCanvasProps = {
  items: CQCanvasItem[];
  highlightedIds?: Set<string>;
  onCardClick?: (cqId: string) => void;
};

/**
 * Spatial explorer composing CQ cards by domain (columns) x CQ type (rows).
 */
export function CQCanvas({
  items,
  highlightedIds = new Set(),
  onCardClick,
}: CQCanvasProps) {
  // Group by domain for columns
  const domains = [...new Set(items.map((i) => i.domain))].sort();
  const cqTypes = [...new Set(items.map((i) => i.cqType))].sort();

  return (
    <div data-testid="cq-canvas" className="overflow-auto">
      <div
        className="grid gap-2"
        style={{
          gridTemplateColumns: `auto ${domains.map(() => "1fr").join(" ")}`,
        }}
      >
        {/* Header row */}
        <div className="p-1 text-xs font-semibold text-slate-500" />
        {domains.map((domain) => (
          <div
            key={domain}
            className="p-1 text-center text-xs font-semibold text-slate-600"
            data-testid={`cq-canvas-domain-${domain}`}
          >
            {domain}
          </div>
        ))}

        {/* Data rows */}
        {cqTypes.map((cqType) => (
          <>
            <div
              key={`label-${cqType}`}
              className="p-1 text-xs font-medium text-slate-500"
              data-testid={`cq-canvas-type-${cqType}`}
            >
              {cqType}
            </div>
            {domains.map((domain) => {
              const cellItems = items.filter(
                (i) => i.domain === domain && i.cqType === cqType,
              );
              return (
                <div
                  key={`${cqType}-${domain}`}
                  className="flex flex-col gap-1 rounded-md border border-dashed border-slate-200 p-1"
                >
                  {cellItems.map((item) => (
                    <CQCard
                      key={item.cqId}
                      cqId={item.cqId}
                      cqText={item.cqText}
                      cqType={item.cqType}
                      domain={item.domain}
                      coverageBand={item.coverageBand}
                      isHighlighted={highlightedIds.has(item.cqId)}
                      onClick={() => onCardClick?.(item.cqId)}
                    />
                  ))}
                </div>
              );
            })}
          </>
        ))}
      </div>
    </div>
  );
}
