// Tests for `frontend/components/recon/DocumentedRealityReport.tsx`
// (Chunk 37, D286 / D288).

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DocumentedRealityReport } from "@/components/recon/DocumentedRealityReport";
import type { DocumentedRealityReportResponse } from "@/lib/api/recon-types";

const FIXTURE: DocumentedRealityReportResponse = {
  report_id: "report-1",
  trigger: "on_demand",
  corpus_below_floor: false,
  narrative: "The graph documents an organization with two top entity types.",
  generated_at: "2026-05-07T00:00:00Z",
  aggregations: {
    top_entities: [{ type_name: "Company", count: 70 }],
    top_relationships: [],
    legal_entities: [],
    monetary_flow: {},
    participants: [],
    business_activity_signature: {},
    total_vertices: 100,
    total_edges: 50,
  },
};

describe("DocumentedRealityReport (Chunk 37, D286 / D288)", () => {
  it("renders the descriptive prose narrative", () => {
    render(<DocumentedRealityReport data={FIXTURE} />);
    expect(
      screen.getByTestId("documented-reality-narrative").textContent,
    ).toContain("organization with two top entity types");
  });

  it("toggles the aggregation panel open and closed", () => {
    render(<DocumentedRealityReport data={FIXTURE} />);
    const toggle = screen.getByTestId("documented-reality-aggregations-toggle");
    expect(screen.queryByTestId("documented-reality-aggregations")).toBeNull();
    fireEvent.click(toggle);
    expect(
      screen.getByTestId("documented-reality-aggregations").textContent,
    ).toContain("100");
    fireEvent.click(toggle);
    expect(screen.queryByTestId("documented-reality-aggregations")).toBeNull();
  });
});
