import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ModeSelectionRationale } from "@/components/review/ModeSelectionRationale";

describe("ModeSelectionRationale", () => {
  it("renders on Structure phase entry (EC-6)", () => {
    render(<ModeSelectionRationale />);
    expect(screen.getByTestId("mode-selection-rationale")).toBeTruthy();
    expect(screen.getByText(/Structure mode selected/)).toBeTruthy();
  });
});
