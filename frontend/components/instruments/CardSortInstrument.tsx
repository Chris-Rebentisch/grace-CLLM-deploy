"use client";
import { InstrumentShell } from "./InstrumentShell";
import { CardSortCanvas, type CardSortItem } from "@/components/cq-canvas/CardSortCanvas";
import { emitTelemetry } from "@/lib/telemetry/bus";

export type CardSortInstrumentProps = {
  cards: CardSortItem[];
  onComplete?: () => void;
};

export function CardSortInstrument({ cards, onComplete }: CardSortInstrumentProps) {
  const startTime = Date.now();
  let recategorizationCount = 0;

  const handleSort = (sorted: CardSortItem[]) => {
    recategorizationCount++;
  };

  const handleComplete = () => {
    const categories = new Set(cards.map((c) => c.category));
    emitTelemetry("card_sort_completed", {
      card_count: cards.length,
      category_count: categories.size,
      recategorization_count: recategorizationCount,
      duration_ms: Date.now() - startTime,
    });
    onComplete?.();
  };

  return (
    <InstrumentShell instrumentName="Card Sort" onComplete={handleComplete}>
      <div data-testid="card-sort-instrument">
        <CardSortCanvas cards={cards} onSort={handleSort} layout="grid" />
      </div>
    </InstrumentShell>
  );
}
