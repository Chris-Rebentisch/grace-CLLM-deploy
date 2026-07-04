"use client";

// D217: rerank/rrf scores are rendered as bar widths only; no numeric label.

import type { RankedResult } from "@/lib/api/types";

export type ResultsRankedListProps = {
  results: RankedResult[];
  selectedIndex: number | null;
  onSelect?: (index: number) => void;
};

function normalize(values: number[]): (v: number) => number {
  if (values.length === 0) return () => 0;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  if (range === 0) return () => 100;
  return (v: number) => ((v - min) / range) * 100;
}

export function ResultsRankedList({
  results,
  selectedIndex,
  onSelect,
}: ResultsRankedListProps) {
  const rerankScores = results.map((r) => r.rerank_score);
  const rerankToPct = normalize(rerankScores);

  if (results.length === 0) {
    return (
      <div
        data-testid="results-ranked-list-empty"
        className="text-xs text-slate-500 p-3"
      >
        No results for the last query.
      </div>
    );
  }

  return (
    <div
      data-testid="results-ranked-list"
      className="overflow-x-auto bg-white border rounded-md"
    >
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b bg-slate-50 text-slate-600">
            <th className="text-left px-3 py-2 font-medium">#</th>
            <th className="text-left px-3 py-2 font-medium">Type</th>
            <th className="text-left px-3 py-2 font-medium">Name</th>
            <th className="text-left px-3 py-2 font-medium">Strategies</th>
            <th className="text-left px-3 py-2 font-medium">Relevance</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r, idx) => {
            const pct = rerankToPct(r.rerank_score);
            const selected = idx === selectedIndex;
            return (
              <tr
                key={r.grace_id}
                data-testid={`result-row-${idx}`}
                onClick={() => onSelect?.(idx)}
                className={
                  selected
                    ? "bg-indigo-50 border-b cursor-pointer"
                    : "border-b hover:bg-slate-50 cursor-pointer"
                }
              >
                <td
                  className="px-3 py-2 font-mono text-slate-500"
                  data-testid={`result-rank-${idx}`}
                >
                  #{idx + 1}
                </td>
                <td className="px-3 py-2 text-slate-700">{r.entity_type}</td>
                <td className="px-3 py-2 text-slate-900">{r.name}</td>
                <td className="px-3 py-2 text-slate-600">
                  {r.contributing_strategies.join(", ")}
                </td>
                <td className="px-3 py-2">
                  {/* Bar width only — D217: no numeric label on score value */}
                  <div
                    data-testid={`result-rerank-bar-${idx}`}
                    className="w-24 h-2 bg-slate-100 rounded-sm overflow-hidden"
                    aria-label="Relevance (normalized)"
                  >
                    <div
                      className="bg-indigo-500 h-full"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
