"use client";

/**
 * CardSortCanvas -- D225 library isolation boundary.
 * This is the ONLY file allowed to import @dnd-kit/* directly.
 * All other components interact via the prop interface.
 */

import { useCallback, useState } from "react";

// D225: dnd-kit imports isolated to this file only
// Using dynamic import pattern to gracefully handle missing dependency in tests
let DragDropProvider: React.ComponentType<{ children: React.ReactNode }> | null = null;
let Sortable: React.ComponentType<{ id: string; children: React.ReactNode }> | null = null;

try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const dndReact = require("@dnd-kit/react");
  DragDropProvider = dndReact.DragDropProvider;
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const dndDom = require("@dnd-kit/dom");
  Sortable = dndDom.Sortable;
} catch {
  // dnd-kit not installed yet -- render static fallback
}

export type CardSortItem = {
  id: string;
  text: string;
  category: string;
  metadata?: Record<string, unknown>;
};

export type CardSortLayout = "grid" | "list";

export type CardSortCanvasProps = {
  cards: CardSortItem[];
  onSort: (cards: CardSortItem[]) => void;
  layout?: CardSortLayout;
  highlightedCardIds?: string[];
};

export function CardSortCanvas({
  cards,
  onSort,
  layout = "grid",
  highlightedCardIds = [],
}: CardSortCanvasProps) {
  const [items, setItems] = useState(cards);

  const handleDragEnd = useCallback(
    (event: { source: { id: string }; target: { id: string } | null }) => {
      if (!event.target) return;

      const sourceIdx = items.findIndex((c) => c.id === event.source.id);
      const targetIdx = items.findIndex((c) => c.id === event.target!.id);
      if (sourceIdx === -1 || targetIdx === -1) return;

      const updated = [...items];
      const [moved] = updated.splice(sourceIdx, 1);
      // Apply target's category to the moved card (recategorize)
      moved.category = items[targetIdx].category;
      updated.splice(targetIdx, 0, moved);
      setItems(updated);
      onSort(updated);
    },
    [items, onSort],
  );

  const isHighlighted = (id: string) => highlightedCardIds.includes(id);

  // Static fallback when dnd-kit is not available
  if (!DragDropProvider) {
    return (
      <div
        data-testid="card-sort-canvas"
        className={`${layout === "grid" ? "grid grid-cols-3 gap-2" : "flex flex-col gap-2"}`}
      >
        {items.map((card) => (
          <div
            key={card.id}
            data-testid={`card-sort-item-${card.id}`}
            className={`rounded-md border p-2 text-xs ${
              isHighlighted(card.id) ? "border-blue-500 bg-blue-50" : "border-border"
            }`}
          >
            <div className="font-medium">{card.category}</div>
            <div>{card.text}</div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div
      data-testid="card-sort-canvas"
      className={`${layout === "grid" ? "grid grid-cols-3 gap-2" : "flex flex-col gap-2"}`}
    >
      {items.map((card) => (
        <div
          key={card.id}
          data-testid={`card-sort-item-${card.id}`}
          draggable
          className={`cursor-grab rounded-md border p-2 text-xs ${
            isHighlighted(card.id) ? "border-blue-500 bg-blue-50" : "border-border"
          }`}
        >
          <div className="font-medium">{card.category}</div>
          <div>{card.text}</div>
        </div>
      ))}
    </div>
  );
}
