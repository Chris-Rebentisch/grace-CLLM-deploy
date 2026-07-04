"use client";

/**
 * SegmentCardSort — D323 hybrid pattern.
 *
 * Card-sort UI for segment rename/merge/split/drop. Imports
 * {@link CardSortCanvas} from the existing CQ canvas (D225 isolation
 * boundary) so we do NOT install or import @dnd-kit/* directly here.
 *
 * Accessibility:
 *   - Spacebar pickup on a focused card (alternate to mouse drag).
 *   - Arrow keys move a "picked-up" card up/down within the list.
 *   - ARIA live region announces pickup / drop / move.
 *   - Non-drag fallback via SegmentMoveMenu (parallel surface).
 */

import { useCallback, useMemo, useRef, useState } from "react";
import { CardSortCanvas, type CardSortItem } from "@/components/cq-canvas/CardSortCanvas";
import { SegmentMoveMenu } from "@/components/decomposition/SegmentMoveMenu";

export type SegmentCard = {
  id: string;
  text: string;
  category: string;
};

export type SegmentCardSortProps = {
  segments: SegmentCard[];
  onChange: (segments: SegmentCard[]) => void;
  /** When true, renders the parallel non-drag move menu. */
  showMoveMenu?: boolean;
};

export function SegmentCardSort({
  segments,
  onChange,
  showMoveMenu = true,
}: SegmentCardSortProps) {
  const [items, setItems] = useState<SegmentCard[]>(segments);
  const [pickedUpId, setPickedUpId] = useState<string | null>(null);
  const [liveText, setLiveText] = useState<string>("");
  const announceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const announce = useCallback((msg: string) => {
    setLiveText(msg);
    if (announceTimer.current) clearTimeout(announceTimer.current);
    announceTimer.current = setTimeout(() => setLiveText(""), 2_500);
  }, []);

  const categories = useMemo(
    () => Array.from(new Set(items.map((s) => s.category))),
    [items],
  );

  const move = useCallback(
    (cardId: string, direction: 1 | -1) => {
      setItems((prev) => {
        const idx = prev.findIndex((c) => c.id === cardId);
        if (idx === -1) return prev;
        const target = idx + direction;
        if (target < 0 || target >= prev.length) return prev;
        const updated = [...prev];
        const [moved] = updated.splice(idx, 1);
        updated.splice(target, 0, moved);
        announce(
          `Segment ${moved.text} moved ${direction === 1 ? "down" : "up"}`,
        );
        onChange(updated);
        return updated;
      });
    },
    [announce, onChange],
  );

  const moveToCategory = useCallback(
    (cardId: string, targetCategory: string) => {
      setItems((prev) => {
        const idx = prev.findIndex((c) => c.id === cardId);
        if (idx === -1) return prev;
        const updated = [...prev];
        updated[idx] = { ...updated[idx], category: targetCategory };
        announce(
          `Segment ${updated[idx].text} moved to category ${targetCategory}`,
        );
        onChange(updated);
        return updated;
      });
    },
    [announce, onChange],
  );

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>, cardId: string) => {
      if (event.key === " " || event.key === "Spacebar") {
        event.preventDefault();
        if (pickedUpId === cardId) {
          setPickedUpId(null);
          announce("Segment dropped");
        } else {
          setPickedUpId(cardId);
          announce("Segment picked up. Use arrow keys to reorder.");
        }
        return;
      }
      if (pickedUpId !== cardId) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        move(cardId, 1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        move(cardId, -1);
      } else if (event.key === "Escape") {
        event.preventDefault();
        setPickedUpId(null);
        announce("Pickup canceled");
      }
    },
    [pickedUpId, move, announce],
  );

  // Adapter: feed CardSortCanvas a CardSortItem shape; mirror sorts back
  // to our richer SegmentCard list (preserves order + category).
  const handleCanvasSort = useCallback(
    (sorted: CardSortItem[]) => {
      const byId = new Map(items.map((s) => [s.id, s]));
      const updated = sorted
        .map((c) => {
          const original = byId.get(c.id);
          if (!original) return null;
          return { ...original, category: c.category };
        })
        .filter((s): s is SegmentCard => s !== null);
      setItems(updated);
      onChange(updated);
    },
    [items, onChange],
  );

  return (
    <div data-testid="segment-card-sort" className="space-y-2">
      <div
        data-testid="segment-card-sort-live"
        role="status"
        aria-live="polite"
        className="sr-only"
      >
        {liveText}
      </div>

      <CardSortCanvas
        cards={items.map((s) => ({
          id: s.id,
          text: s.text,
          category: s.category,
        }))}
        onSort={handleCanvasSort}
        layout="list"
      />

      {/* Keyboard pickup wrappers — separate from the dnd canvas */}
      <ul className="space-y-1">
        {items.map((card) => (
          <li key={card.id}>
            <div
              data-testid={`segment-keyboard-card-${card.id}`}
              tabIndex={0}
              role="button"
              aria-pressed={pickedUpId === card.id}
              aria-label={`${card.text} in ${card.category}. Press Space to pick up.`}
              onKeyDown={(e) => handleKeyDown(e, card.id)}
              className={`flex items-center justify-between rounded border px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-blue-300 ${
                pickedUpId === card.id
                  ? "border-blue-400 bg-blue-50"
                  : "border-slate-200 bg-white"
              }`}
            >
              <span>
                <span className="font-medium">{card.category}</span>
                <span className="ml-2 text-slate-700">{card.text}</span>
              </span>
              {showMoveMenu ? (
                <SegmentMoveMenu
                  cardId={card.id}
                  currentCategory={card.category}
                  categories={categories.length > 1 ? categories : [card.category, "_other"]}
                  onMove={moveToCategory}
                />
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
