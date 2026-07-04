import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { EvidenceBundleSummary } from "@/components/permissions/EvidenceBundleSummary";

describe("<EvidenceBundleSummary />", () => {
  it("is collapsed by default and renders the empty hint when no sections", () => {
    render(
      <EvidenceBundleSummary evidenceId="ev-1" sections={[]} />,
    );
    expect(
      screen.getByTestId("evidence-bundle-summary-ev-1"),
    ).toBeInTheDocument();
    expect(screen.getByText("No evidence sections collected for this cluster yet.")).toBeInTheDocument();
  });

  it("renders one row per section with band labels only (no numerics)", () => {
    render(
      <EvidenceBundleSummary
        evidenceId="ev-2"
        defaultOpen
        sections={[
          {
            source_id: "src-a",
            display_name: "Source A",
            item_count: 4,
            band: "strong",
          },
          {
            source_id: "src-b",
            display_name: "Source B",
            item_count: 0,
            band: null,
          },
        ]}
      />,
    );
    expect(
      screen.getByTestId("evidence-bundle-section-ev-2-src-a"),
    ).toBeInTheDocument();
    expect(
      screen.getByTestId("evidence-bundle-section-ev-2-src-b"),
    ).toBeInTheDocument();
    const text = screen.getByTestId("evidence-bundle-summary-ev-2").textContent ?? "";
    expect(/\b0\.\d+\b/.test(text)).toBe(false);
  });
});
