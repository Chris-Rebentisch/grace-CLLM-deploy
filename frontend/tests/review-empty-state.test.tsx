import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReviewEmptyState } from "@/components/review/ReviewEmptyState";

describe("ReviewEmptyState", () => {
  it("renders empty + error states", () => {
    render(<ReviewEmptyState />);
    expect(screen.getByTestId("review-empty-state")).toBeTruthy();
    expect(screen.getByText(/No active review session/)).toBeTruthy();
  });
});
