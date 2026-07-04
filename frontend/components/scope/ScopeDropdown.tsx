"use client";

import { useScopeSegments } from "@/lib/query/scope";
import { useScopeStore } from "@/lib/state/scope-store";

export function ScopeDropdown() {
  const { data: segments, isLoading } = useScopeSegments();
  const { selectedSegments, isAllSegments, toggleSegment, selectAll } =
    useScopeStore();

  if (isLoading) {
    return (
      <div
        data-testid="scope-dropdown"
        className="rounded-md border border-border bg-white p-2 text-xs shadow-md"
      >
        Loading segments...
      </div>
    );
  }

  const sorted = [...(segments ?? [])].sort((a, b) =>
    a.module_name.localeCompare(b.module_name),
  );

  return (
    <div
      data-testid="scope-dropdown"
      className="absolute right-0 top-full z-50 mt-1 min-w-[200px] rounded-md border border-border bg-white p-2 text-xs shadow-md"
    >
      <label className="flex cursor-pointer items-center gap-2 py-1">
        <input
          type="checkbox"
          checked={isAllSegments}
          onChange={() => selectAll()}
          data-testid="scope-all-segments"
        />
        <span className="font-medium">All segments</span>
      </label>
      <hr className="my-1 border-border" />
      {sorted.map((seg) => (
        <label
          key={seg.module_name}
          className="flex cursor-pointer items-center gap-2 py-1"
        >
          <input
            type="checkbox"
            checked={
              isAllSegments || selectedSegments.includes(seg.module_name)
            }
            onChange={() => toggleSegment(seg.module_name)}
            data-testid={`scope-segment-${seg.module_name}`}
          />
          <span>{seg.module_name}</span>
          <span className="ml-auto text-slate-400" data-testid={`scope-count-${seg.module_name}`}>
            {seg.entity_count}
          </span>
        </label>
      ))}
    </div>
  );
}
