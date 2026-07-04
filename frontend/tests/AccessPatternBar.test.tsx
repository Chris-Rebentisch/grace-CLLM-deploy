import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import {
  AccessPatternBar,
  type AccessPatternCell,
} from "@/components/permissions/AccessPatternBar";

const cells: AccessPatternCell[] = [
  {
    segment_id: "s-1",
    layer: "L0",
    artifact_class: "Schema",
    decision: "allow",
  },
  {
    segment_id: "s-1",
    layer: "L1",
    artifact_class: "Directive",
    decision: "deny",
  },
  {
    segment_id: "s-2",
    layer: "L2",
    artifact_class: "Snapshot",
    decision: "inherit",
  },
];

describe("<AccessPatternBar />", () => {
  it("renders one cell per access decision with the decision label only", () => {
    render(<AccessPatternBar clusterId="c-1" cells={cells} />);
    expect(screen.getByTestId("access-pattern-bar-c-1")).toBeInTheDocument();
    expect(screen.getAllByText("allow", { exact: false }).length).toBeGreaterThan(
      0,
    );
    // No numeric scores should appear.
    const text = screen.getByTestId("access-pattern-bar-c-1").textContent ?? "";
    expect(/\b0\.\d+\b/.test(text)).toBe(false);
  });

  it("renders an empty hint when no cells are provided", () => {
    render(<AccessPatternBar clusterId="c-empty" cells={[]} />);
    expect(screen.getByText("No access cells defined")).toBeInTheDocument();
  });
});
