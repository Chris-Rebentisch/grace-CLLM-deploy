import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReliabilityChart } from "@/components/autonomy/ReliabilityChart";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

describe("ReliabilityChart", () => {
  it("renders empty state when no bands", () => {
    render(<ReliabilityChart bands={[]} />);
    expect(
      screen.getByTestId("reliability-chart-empty"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(AUTONOMY_COPY.reliabilityEmpty),
    ).toBeInTheDocument();
  });

  it("renders one bar per band", () => {
    const bands = [
      { band_low: 0.0, band_high: 0.1, approval_rate: 0.3, sample_count: 5 },
      { band_low: 0.1, band_high: 0.2, approval_rate: 0.9, sample_count: 10 },
    ];
    render(<ReliabilityChart bands={bands} />);
    expect(screen.getByTestId("reliability-band-0")).toBeInTheDocument();
    expect(screen.getByTestId("reliability-band-1")).toBeInTheDocument();
  });

  it("renders band range labels", () => {
    const bands = [
      { band_low: 0.0, band_high: 0.1, approval_rate: 0.5, sample_count: 5 },
    ];
    render(<ReliabilityChart bands={bands} />);
    expect(screen.getByTestId("band-range-0").textContent).toBe("0\u201310%");
  });

  it("maps approval rates to band labels without numeric DOM content", () => {
    const bands = [
      { band_low: 0.0, band_high: 0.1, approval_rate: 0.9, sample_count: 10 },
      { band_low: 0.1, band_high: 0.2, approval_rate: 0.6, sample_count: 8 },
      { band_low: 0.2, band_high: 0.3, approval_rate: 0.2, sample_count: 3 },
    ];
    render(<ReliabilityChart bands={bands} />);
    expect(screen.getByTestId("band-label-0").textContent).toBe(
      AUTONOMY_COPY.bandApprovalHigh,
    );
    expect(screen.getByTestId("band-label-1").textContent).toBe(
      AUTONOMY_COPY.bandApprovalMedium,
    );
    expect(screen.getByTestId("band-label-2").textContent).toBe(
      AUTONOMY_COPY.bandApprovalLow,
    );
  });

  it("never renders raw approval_rate or sample_count in DOM", () => {
    const bands = [
      { band_low: 0.0, band_high: 0.1, approval_rate: 0.87654, sample_count: 42 },
    ];
    const { container } = render(<ReliabilityChart bands={bands} />);
    const text = container.textContent ?? "";
    // D120/D217: no raw approval rate floats in DOM
    expect(text).not.toContain("0.87654");
    expect(text).not.toContain("87654");
    // sample_count must not surface either
    expect(text).not.toContain("42");
  });

  it("honors a custom testId override", () => {
    render(<ReliabilityChart bands={[]} testId="custom-chart" />);
    expect(screen.getByTestId("custom-chart-empty")).toBeInTheDocument();
  });
});
