import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { CQCanvas, type CQCanvasItem } from "@/components/cq-canvas/CQCanvas";

function makeItems(): CQCanvasItem[] {
  return [
    { cqId: "cq-1", cqText: "What entities exist?", cqType: "coverage", domain: "finance", coverageBand: "green", dependentTypes: ["Legal_Entity"] },
    { cqId: "cq-2", cqText: "How are they related?", cqType: "relationship", domain: "finance", coverageBand: "amber", dependentTypes: [] },
    { cqId: "cq-3", cqText: "Temporal constraints?", cqType: "temporal", domain: "legal", coverageBand: "red", dependentTypes: [] },
  ];
}

describe("CQCanvas", () => {
  it("renders domain x CQ-type grid", () => {
    render(<CQCanvas items={makeItems()} />);
    const canvas = screen.getByTestId("cq-canvas");
    expect(canvas).toBeTruthy();
    expect(screen.getByTestId("cq-canvas-domain-finance")).toBeTruthy();
    expect(screen.getByTestId("cq-canvas-domain-legal")).toBeTruthy();
    expect(screen.getByTestId("cq-canvas-type-coverage")).toBeTruthy();
  });

  it("applies coverage coloring via data-coverage-band attribute", () => {
    render(<CQCanvas items={makeItems()} />);
    const card = screen.getByTestId("cq-card-cq-1");
    expect(card.getAttribute("data-coverage-band")).toBe("green");
    const card2 = screen.getByTestId("cq-card-cq-3");
    expect(card2.getAttribute("data-coverage-band")).toBe("red");
  });
});
