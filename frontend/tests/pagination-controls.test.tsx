import { beforeEach, describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PaginationControls } from "@/components/graph/PaginationControls";
import { useGraphStore } from "@/lib/state/graph-store";

beforeEach(() => {
  useGraphStore.getState().reset();
});

describe("PaginationControls", () => {
  it("next button disabled when no next_cursor", () => {
    render(<PaginationControls nextCursor={null} />);
    const next = screen.getByTestId("pagination-next") as HTMLButtonElement;
    expect(next.disabled).toBe(true);
    expect(screen.getByTestId("pagination-state").textContent).toBe("Page 1");
  });

  it("next button advances the store cursor when clicked", () => {
    render(<PaginationControls nextCursor="cursor-xyz" />);
    const next = screen.getByTestId("pagination-next") as HTMLButtonElement;
    expect(next.disabled).toBe(false);
    fireEvent.click(next);
    expect(useGraphStore.getState().paginationCursor).toBe("cursor-xyz");
  });
});
