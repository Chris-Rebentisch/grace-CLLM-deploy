import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { TrustIndicatorBadge } from "@/components/autonomy/TrustIndicator";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

describe("TrustIndicatorBadge", () => {
  it("renders the three indicator labels and never numeric DOM", () => {
    for (const indicator of ["high", "building", "insufficient"] as const) {
      const { unmount } = render(
        <TrustIndicatorBadge indicator={indicator} />,
      );
      const el = screen.getByTestId(`trust-indicator-${indicator}`);
      expect(el).toBeInTheDocument();
      // Band label only — no decimal numerals.
      expect(el.textContent ?? "").not.toMatch(/0?\.\d/);
      unmount();
    }
  });

  it("maps indicator values to copy labels", () => {
    const { unmount } = render(<TrustIndicatorBadge indicator="high" />);
    expect(
      screen.getByTestId("trust-indicator-high").textContent,
    ).toBe(AUTONOMY_COPY.trustIndicatorHigh);
    unmount();

    const { unmount: u2 } = render(
      <TrustIndicatorBadge indicator="building" />,
    );
    expect(
      screen.getByTestId("trust-indicator-building").textContent,
    ).toBe(AUTONOMY_COPY.trustIndicatorBuilding);
    u2();

    render(<TrustIndicatorBadge indicator="insufficient" />);
    expect(
      screen.getByTestId("trust-indicator-insufficient").textContent,
    ).toBe(AUTONOMY_COPY.trustIndicatorInsufficient);
  });

  it("honors a custom testId override", () => {
    render(<TrustIndicatorBadge indicator="high" testId="custom-trust" />);
    expect(screen.getByTestId("custom-trust")).toBeInTheDocument();
  });
});
