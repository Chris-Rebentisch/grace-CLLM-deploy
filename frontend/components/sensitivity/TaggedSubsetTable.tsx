"use client";

/**
 * TaggedSubsetTable — render-only display of cluster decisions filtered
 * by non-empty `sensitivity_tags` (D343).
 *
 * Hard invariant: this component has zero admission semantics and never
 * makes a request — it consumes the in-memory `TaggedSubset` produced
 * by `sensitivityApi.projectTaggedSubset()`. The Sensitivity Gate is a
 * render surface over the Chunk 42 Permission Matrix engine (D270).
 */

import type { TaggedSubset } from "@/lib/api/types";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";

export type TaggedSubsetTableProps = {
  subset: TaggedSubset;
};

export function TaggedSubsetTable({ subset }: TaggedSubsetTableProps) {
  const rows = subset.cluster_decisions;
  if (rows.length === 0) {
    return (
      <p
        data-testid="tagged-subset-empty"
        className="text-xs italic text-slate-500"
      >
        {SENSITIVITY_COPY.taggedSubsetEmpty}
      </p>
    );
  }
  return (
    <table
      data-testid="tagged-subset-table"
      className="w-full table-auto border-collapse text-[11px]"
    >
      <thead>
        <tr className="border-b border-slate-200 text-left text-slate-600">
          <th className="px-2 py-1">Cluster</th>
          <th className="px-2 py-1">Resource</th>
          <th className="px-2 py-1">Action</th>
          <th className="px-2 py-1">Decision</th>
          <th className="px-2 py-1">Tags</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, idx) => (
          <tr
            key={`${row.cluster_id}-${row.resource_kind}-${row.resource_label}-${row.action}-${idx}`}
            data-testid={`tagged-subset-row-${row.cluster_id}-${idx}`}
            className="border-b border-slate-100"
          >
            <td className="px-2 py-1 align-top">
              <div className="font-semibold text-slate-900">
                {row.cluster_display_name}
              </div>
              <div className="font-mono text-[10px] text-slate-500">
                {row.cluster_id}
              </div>
            </td>
            <td className="px-2 py-1 align-top">
              <div className="text-slate-900">{row.resource_label}</div>
              <div className="text-[10px] text-slate-500">
                {row.resource_kind}
              </div>
            </td>
            <td className="px-2 py-1 align-top">{row.action}</td>
            <td
              data-testid={`tagged-subset-decision-${row.cluster_id}-${idx}`}
              className={`px-2 py-1 align-top font-semibold ${
                row.decision === "allow"
                  ? "text-emerald-700"
                  : "text-rose-700"
              }`}
            >
              {row.decision}
            </td>
            <td className="px-2 py-1 align-top">
              <ul className="flex flex-wrap gap-1">
                {row.sensitivity_tags.map((tag) => (
                  <li
                    key={tag.name}
                    data-testid={`tagged-subset-tag-${tag.name}`}
                    className="rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 text-[10px] text-slate-700"
                  >
                    {tag.name}
                  </li>
                ))}
              </ul>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
