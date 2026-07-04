import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { LatencyBreakdown } from "@/components/inspector/LatencyBreakdown";

describe("LatencyBreakdown", () => {
  it("renders per-component ms numerals (allowed per D217)", () => {
    render(
      <LatencyBreakdown
        latencyMs={{ graph: 412.6, semantic: 230.1, bm25: 50.0, rerank: 80.0 }}
      />,
    );
    expect(screen.getByTestId("latency-breakdown")).toBeTruthy();
    expect(screen.getByTestId("latency-graph").textContent).toBe("413 ms");
    expect(screen.getByTestId("latency-semantic").textContent).toBe("230 ms");
  });

  it("renders total latency label", () => {
    render(<LatencyBreakdown latencyMs={{ graph: 100, semantic: 200 }} />);
    expect(screen.getByTestId("latency-total-ms").textContent).toBe(
      "total: 300 ms",
    );
  });
});
