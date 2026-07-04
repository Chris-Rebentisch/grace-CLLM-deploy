"use client";

// D217: counts are allowed numerals; RRF / rerank scores are not rendered.

export type StrategyBreakdownChartProps = {
  contributions: Record<string, number>;
};

export function StrategyBreakdownChart({
  contributions,
}: StrategyBreakdownChartProps) {
  const entries = Object.entries(contributions ?? {}).sort(
    (a, b) => b[1] - a[1],
  );
  const total = entries.reduce((acc, [, v]) => acc + v, 0);

  if (entries.length === 0) {
    return (
      <div
        data-testid="strategy-breakdown-empty"
        className="text-xs text-slate-500 p-3"
      >
        No strategies fired.
      </div>
    );
  }

  return (
    <div
      data-testid="strategy-breakdown-chart"
      className="space-y-1.5 p-3 bg-white border rounded-md"
    >
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wide">
        Strategy contributions
      </h3>
      <ul className="space-y-1">
        {entries.map(([strategy, count]) => {
          const pct = total > 0 ? (count / total) * 100 : 0;
          return (
            <li key={strategy} className="flex items-center gap-2 text-xs">
              <span
                className="text-slate-700 min-w-[80px]"
                data-testid={`strategy-name-${strategy}`}
              >
                {strategy}
              </span>
              <div className="flex-1 bg-slate-100 rounded-sm h-3 overflow-hidden">
                <div
                  className="bg-indigo-500 h-full"
                  style={{ width: `${pct}%` }}
                />
              </div>
              <span
                className="text-slate-600 min-w-[60px] text-right"
                data-testid={`strategy-count-${strategy}`}
              >
                {count} results
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
