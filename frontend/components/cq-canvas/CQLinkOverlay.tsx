"use client";
import { useState } from "react";

export type LinkItem = { id: string; dependentIds: string[] };

export type CQLinkOverlayProps = {
  items: LinkItem[];
  onHighlight: (ids: Set<string>) => void;
};

export function CQLinkOverlay({ items, onHighlight }: CQLinkOverlayProps) {
  const [activeId, setActiveId] = useState<string | null>(null);

  const handleClick = (id: string) => {
    const item = items.find((i) => i.id === id);
    if (!item) return;
    if (activeId === id) {
      setActiveId(null);
      onHighlight(new Set());
    } else {
      setActiveId(id);
      onHighlight(new Set(item.dependentIds));
    }
  };

  return (
    <div data-testid="cq-link-overlay" className="flex flex-wrap gap-1">
      {items.map((item) => (
        <button key={item.id} type="button" onClick={() => handleClick(item.id)} data-testid={`link-item-${item.id}`} className={`rounded px-2 py-0.5 text-[10px] ${activeId === item.id ? "bg-blue-100 text-blue-700" : "bg-slate-100 text-slate-600"}`}>
          {item.id} ({item.dependentIds.length})
        </button>
      ))}
    </div>
  );
}
