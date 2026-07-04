"use client";

// D217: ms numerals are allowed (operational, not confidence).

export type LatencyBreakdownProps = {
  latencyMs: Record<string, number>;
};

export function LatencyBreakdown({ latencyMs }: LatencyBreakdownProps) {
  const entries = Object.entries(latencyMs ?? {}).filter(
    ([key]) => key !== "total",
  );
  const total = Object.values(latencyMs ?? {}).reduce(
    (acc, v) => acc + (typeof v === "number" ? v : 0),
    0,
  );

  if (entries.length === 0) {
    return (
      <div
        data-testid="latency-breakdown-empty"
        className="text-xs text-slate-500 p-3 bg-white border rounded-md"
      >
        No latency data.
      </div>
    );
  }

  return (
    <div
      data-testid="latency-breakdown"
      className="bg-white border rounded-md p-3 space-y-2"
    >
      <header className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
          Latency breakdown
        </h3>
        <span
          className="text-xs text-slate-600"
          data-testid="latency-total-ms"
        >
          total: {Math.round(total)} ms
        </span>
      </header>
      <ul className="space-y-1">
        {entries.map(([component, ms]) => {
          const pct = total > 0 ? (ms / total) * 100 : 0;
          return (
            <li key={component} className="flex items-center gap-2 text-xs">
              <span className="text-slate-700 min-w-[100px]">{component}</span>
              <div className="flex-1 bg-slate-100 rounded-sm h-3 overflow-hidden">
                <div
                  className="bg-amber-500 h-full"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span
                className="text-slate-600 min-w-[70px] text-right"
                data-testid={`latency-${component}`}
              >
                {Math.round(ms)} ms
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
