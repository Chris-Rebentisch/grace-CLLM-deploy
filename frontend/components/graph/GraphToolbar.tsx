"use client";

import { useGraphStore } from "@/lib/state/graph-store";
import type { LayoutName } from "@/lib/graph/layout-adapters";

export function GraphToolbar() {
  const layout = useGraphStore((s) => s.activeLayout);
  const setLayout = useGraphStore((s) => s.setLayout);

  const onChange = (l: LayoutName) => {
    setLayout(l);
  };

  return (
    <div
      data-testid="graph-toolbar"
      className="flex items-center gap-3 px-3 py-2 border-b bg-white"
    >
      <span className="text-xs font-medium text-slate-600">Layout:</span>
      <button
        type="button"
        data-testid="layout-fcose"
        className={
          layout === "fcose"
            ? "rounded-md bg-slate-800 text-white px-3 py-1 text-xs"
            : "rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-1 text-xs"
        }
        onClick={() => onChange("fcose")}
      >
        Force-directed
      </button>
      <button
        type="button"
        data-testid="layout-dagre"
        className={
          layout === "dagre"
            ? "rounded-md bg-slate-800 text-white px-3 py-1 text-xs"
            : "rounded-md border border-slate-300 bg-white text-slate-700 px-3 py-1 text-xs"
        }
        onClick={() => onChange("dagre")}
      >
        Hierarchical
      </button>
    </div>
  );
}
