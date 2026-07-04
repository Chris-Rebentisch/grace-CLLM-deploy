import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { CQCard } from "@/components/cq-canvas/CQCard";

describe("CQCard", () => {
  it("renders coverage color band only, no numeric label (D217)", () => {
    render(
      <CQCard
        cqId="cq-1"
        cqText="Does the ontology represent financial entities?"
        cqType="coverage"
        domain="finance"
        coverageBand="green"
      />,
    );
    const card = screen.getByTestId("cq-card-cq-1");
    expect(card.getAttribute("data-coverage-band")).toBe("green");
    // No numeric label should appear -- coverage is visual only per D217
    const text = card.textContent ?? "";
    expect(text).not.toMatch(/\d+%/);
    expect(text).not.toMatch(/extraction_confidence/);
    expect(text).not.toMatch(/rrf_score/);
  });
});
