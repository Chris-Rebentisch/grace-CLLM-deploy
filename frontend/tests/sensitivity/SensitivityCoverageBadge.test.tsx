import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { SensitivityCoverageBadge } from "@/components/sensitivity/SensitivityCoverageBadge";
import { SENSITIVITY_COPY } from "@/lib/sensitivity/copy";

describe("SensitivityCoverageBadge", () => {
  it("renders the three band labels and never numeric DOM", () => {
    for (const band of ["high", "medium", "low"] as const) {
      const { unmount } = render(<SensitivityCoverageBadge band={band} />);
      const el = screen.getByTestId(`sensitivity-coverage-badge-${band}`);
      expect(el).toBeInTheDocument();
      // Band label is the only textual content; no decimal numerals.
      expect(el.textContent ?? "").not.toMatch(/0?\.\d/);
      unmount();
    }
  });

  it("renders the below-floor variant when band is null", () => {
    render(<SensitivityCoverageBadge band={null} />);
    const el = screen.getByTestId("sensitivity-coverage-badge-below-floor");
    expect(el).toBeInTheDocument();
    expect(el.textContent).toContain(SENSITIVITY_COPY.coverageBandUnknown);
  });

  it("honors a custom testId override", () => {
    render(<SensitivityCoverageBadge band="high" testId="custom-id" />);
    expect(screen.getByTestId("custom-id")).toBeInTheDocument();
  });
});
