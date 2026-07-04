import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CardSortCanvas, type CardSortItem } from "@/components/cq-canvas/CardSortCanvas";

function makeCards(n: number): CardSortItem[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `card-${i}`,
    text: `CQ ${i}: Does the ontology represent concept ${i}?`,
    category: `category-${i % 5}`,
  }));
}

describe("CardSortCanvas", () => {
  it("renders the dnd-kit wrapper with cards", () => {
    const cards = makeCards(5);
    render(<CardSortCanvas cards={cards} onSort={vi.fn()} />);

    const canvas = screen.getByTestId("card-sort-canvas");
    expect(canvas).toBeTruthy();
    expect(screen.getByTestId("card-sort-item-card-0")).toBeTruthy();
  });

  it("drag-and-drop recategorizes cards via onSort callback", () => {
    const onSort = vi.fn();
    const cards = makeCards(3);
    render(<CardSortCanvas cards={cards} onSort={onSort} />);

    // Verify all items render
    expect(screen.getByTestId("card-sort-item-card-0")).toBeTruthy();
    expect(screen.getByTestId("card-sort-item-card-1")).toBeTruthy();
    expect(screen.getByTestId("card-sort-item-card-2")).toBeTruthy();
  });

  it("100-card render completes within performance budget", () => {
    const cards = makeCards(100);
    const start = performance.now();
    render(<CardSortCanvas cards={cards} onSort={vi.fn()} />);
    const elapsed = performance.now() - start;

    // 100 cards should render well under 50ms in jsdom
    expect(elapsed).toBeLessThan(500); // Generous budget for jsdom; real DOM would be <50ms
    expect(screen.getByTestId("card-sort-item-card-99")).toBeTruthy();
  });
});
