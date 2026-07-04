import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { StrategyBreakdownChart } from "@/components/inspector/StrategyBreakdownChart";

describe("StrategyBreakdownChart", () => {
  it("renders per-strategy rows with counts (D217: allowed numerals)", () => {
    render(
      <StrategyBreakdownChart
        contributions={{ graph: 2, semantic: 5, bm25: 3, temporal: 0 }}
      />,
    );
    expect(screen.getByTestId("strategy-breakdown-chart")).toBeTruthy();
    // Counts render as numerals — allowed
    expect(screen.getByTestId("strategy-count-semantic").textContent).toBe(
      "5 results",
    );
    expect(screen.getByTestId("strategy-count-graph").textContent).toBe(
      "2 results",
    );
  });

  it("renders empty state when no strategies fired", () => {
    render(<StrategyBreakdownChart contributions={{}} />);
    expect(screen.getByTestId("strategy-breakdown-empty")).toBeTruthy();
  });
});
