"use client";

import { useState } from "react";
import type { CriterionEvidenceResult } from "@/lib/api/types";

export function EvidenceCriterionResultsTable({
  rows,
}: {
  rows: CriterionEvidenceResult[];
}) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div data-testid="evidence-criterion-table" className="overflow-x-auto">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b bg-slate-50 text-left">
            <th className="p-2">Criterion</th>
            <th className="p-2">Met</th>
            <th className="p-2">Observed</th>
            <th className="p-2">Samples</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.criterion_id} className="border-b">
              <td className="max-w-[14rem] truncate p-2 font-mono text-[10px]">
                {r.criterion_id}
              </td>
              <td className="p-2">{r.satisfied ? "yes" : "no"}</td>
              <td className="p-2 whitespace-nowrap">{r.query_executed_at}</td>
              <td className="p-2">
                <button
                  type="button"
                  className="text-blue-700 underline"
                  data-testid={`toggle-samples-${r.criterion_id}`}
                  onClick={() =>
                    setOpen((v) => (v === r.criterion_id ? null : r.criterion_id))
                  }
                >
                  {open === r.criterion_id ? "Hide" : "Show"} IDs
                </button>
                {open === r.criterion_id ? (
                  <ul className="mt-1 list-inside list-disc font-mono text-[10px]">
                    {r.sample_grace_ids.map((id) => (
                      <li key={id}>{id}</li>
                    ))}
                  </ul>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
