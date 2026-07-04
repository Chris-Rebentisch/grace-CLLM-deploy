// Chunk 41 D323 — SegmentCardSort hybrid pattern (drag + keyboard + non-drag fallback).

import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

import { SegmentCardSort } from "@/components/decomposition/SegmentCardSort";

const FIXTURE = [
  { id: "s1", text: "Finance", category: "operations" },
  { id: "s2", text: "Engineering", category: "delivery" },
  { id: "s3", text: "Sales", category: "operations" },
];

describe("SegmentCardSort (Chunk 41 D323)", () => {
  it("renders one keyboard-accessible card per segment", () => {
    render(<SegmentCardSort segments={FIXTURE} onChange={() => {}} />);
    expect(screen.getByTestId("segment-keyboard-card-s1")).toBeTruthy();
    expect(screen.getByTestId("segment-keyboard-card-s2")).toBeTruthy();
    expect(screen.getByTestId("segment-keyboard-card-s3")).toBeTruthy();
  });

  it("Spacebar pickup toggles aria-pressed on the focused card", () => {
    render(<SegmentCardSort segments={FIXTURE} onChange={() => {}} />);
    const card = screen.getByTestId("segment-keyboard-card-s1");
    expect(card.getAttribute("aria-pressed")).toBe("false");
    fireEvent.keyDown(card, { key: " " });
    expect(card.getAttribute("aria-pressed")).toBe("true");
    fireEvent.keyDown(card, { key: " " });
    expect(card.getAttribute("aria-pressed")).toBe("false");
  });

  it("ArrowDown moves the picked-up card and announces via the live region", () => {
    const onChange = vi.fn();
    render(<SegmentCardSort segments={FIXTURE} onChange={onChange} />);
    const first = screen.getByTestId("segment-keyboard-card-s1");
    fireEvent.keyDown(first, { key: " " });
    fireEvent.keyDown(first, { key: "ArrowDown" });
    expect(onChange).toHaveBeenCalled();
    const live = screen.getByTestId("segment-card-sort-live");
    expect(live.textContent ?? "").toMatch(/moved/i);
  });

  it("non-drag move menu reassigns category", () => {
    const onChange = vi.fn();
    render(<SegmentCardSort segments={FIXTURE} onChange={onChange} />);
    const select = screen.getByTestId("segment-move-select-s1") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "delivery" } });
    fireEvent.click(screen.getByTestId("segment-move-apply-s1"));
    expect(onChange).toHaveBeenCalled();
    const calls = onChange.mock.calls;
    const last = calls[calls.length - 1][0] as Array<{ id: string; category: string }>;
    const moved = last.find((s) => s.id === "s1");
    expect(moved?.category).toBe("delivery");
  });

  it("ARIA live region is rendered with role=status and polite politeness", () => {
    render(<SegmentCardSort segments={FIXTURE} onChange={() => {}} />);
    const live = screen.getByTestId("segment-card-sort-live");
    expect(live.getAttribute("role")).toBe("status");
    expect(live.getAttribute("aria-live")).toBe("polite");
  });
});
