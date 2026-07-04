"use client";

/**
 * SegmentMoveMenu — D323 non-drag fallback.
 *
 * Accessibility-first parallel path to {@link SegmentCardSort}: the
 * operator can move a card to any other category via a plain
 * <select> element. No pointer events, no dnd-kit primitives.
 */

import { useState } from "react";

export type SegmentMoveMenuProps = {
  cardId: string;
  currentCategory: string;
  categories: string[];
  onMove: (cardId: string, targetCategory: string) => void;
};

export function SegmentMoveMenu({
  cardId,
  currentCategory,
  categories,
  onMove,
}: SegmentMoveMenuProps) {
  const [target, setTarget] = useState<string>(currentCategory);

  const choices = categories.filter((c) => c !== currentCategory);

  return (
    <div
      data-testid={`segment-move-menu-${cardId}`}
      className="flex items-center gap-1 text-xs"
    >
      <label htmlFor={`move-select-${cardId}`} className="text-slate-600">
        Move to
      </label>
      <select
        id={`move-select-${cardId}`}
        data-testid={`segment-move-select-${cardId}`}
        value={target}
        onChange={(e) => setTarget(e.target.value)}
        className="rounded border border-slate-300 px-1 py-0.5"
      >
        <option value={currentCategory}>{currentCategory}</option>
        {choices.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>
      <button
        type="button"
        data-testid={`segment-move-apply-${cardId}`}
        disabled={target === currentCategory}
        onClick={() => onMove(cardId, target)}
        className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-blue-900 disabled:opacity-40"
      >
        Apply
      </button>
    </div>
  );
}
