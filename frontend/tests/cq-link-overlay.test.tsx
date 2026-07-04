import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CQLinkOverlay } from "@/components/cq-canvas/CQLinkOverlay";

describe("CQLinkOverlay", () => {
  it("highlights dependent CQs/types on click", () => {
    const onHighlight = vi.fn();
    render(<CQLinkOverlay items={[{ id: "cq-1", dependentIds: ["type-A", "type-B"] }, { id: "type-A", dependentIds: ["cq-1"] }]} onHighlight={onHighlight} />);
    fireEvent.click(screen.getByTestId("link-item-cq-1"));
    expect(onHighlight).toHaveBeenCalledWith(new Set(["type-A", "type-B"]));
  });
});
