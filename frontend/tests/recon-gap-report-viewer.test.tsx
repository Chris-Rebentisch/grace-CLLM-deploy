// Tests for `frontend/components/recon/GapReportViewer.tsx` (Chunk 36 D278 / Chunk 37 D288).

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { GapReportViewer } from "@/components/recon/GapReportViewer";
import type { GapReportResponse } from "@/lib/api/recon-types";

const FIXTURE: GapReportResponse = {
  session_id: "sess-1",
  reviewer: "alice",
  generated_at: "2026-05-07T00:00:00Z",
  evidence_grounding_score: 0.6,
  evidence_grounding_threshold: 0.5,
  graph_population_floor_breach: null,
  emphasized_with_evidence: [
    { element_name: "Company", element_type: "entity", instance_count: 12, top_evidence_extraction_event_ids: [] },
  ],
  emphasized_without_evidence: [
    { element_name: "Trust", element_type: "entity", instance_count: 0, suggested_actions: ["Add evidence"] },
  ],
  unemphasized_in_evidence: [
    { element_name: "Person", element_type: "entity", instance_count: 5, decision_status: "skipped" },
  ],
  covering_directives: [],
};

describe("GapReportViewer (Chunk 36 D278 / Chunk 37 D288)", () => {
  it("renders all three sections when data is present", () => {
    render(<GapReportViewer data={FIXTURE} />);
    expect(
      screen.getByTestId("gap-report-emphasized-with-evidence").textContent,
    ).toContain("Company");
    expect(
      screen.getByTestId("gap-report-emphasized-without-evidence").textContent,
    ).toContain("Trust");
    expect(
      screen.getByTestId("gap-report-unemphasized-in-evidence").textContent,
    ).toContain("Person");
  });

  it("renders the empty state when data is null", () => {
    render(<GapReportViewer data={null} />);
    expect(screen.getByTestId("gap-report-empty")).toBeTruthy();
  });
});
