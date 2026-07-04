// D297 (Chunk 38) — recon "change-in-flight" framing tests.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { GapReportViewer } from "@/components/recon/GapReportViewer";
import { DivergenceMap } from "@/components/recon/DivergenceMap";
import type {
  DivergenceMapResponse,
  GapReportResponse,
} from "@/lib/api/recon-types";
import type { CoveringDirective } from "@/lib/api/types";

const SAMPLE_DIRECTIVE: CoveringDirective = {
  directive_id: "11111111-1111-1111-1111-111111111111",
  tier: "Operational_Adjustment",
  title: "Reorganized client tier",
  status: "active",
  authored_at: "2026-04-01T00:00:00Z",
  affected_segments: ["Legal_Entity"],
};

const GAP_REPORT_BASE: GapReportResponse = {
  session_id: "sess-1",
  reviewer: "alice",
  generated_at: "2026-05-07T00:00:00Z",
  evidence_grounding_score: 0.6,
  evidence_grounding_threshold: 0.5,
  graph_population_floor_breach: null,
  emphasized_with_evidence: [],
  emphasized_without_evidence: [],
  unemphasized_in_evidence: [],
  covering_directives: [],
};

const DIVERGENCE_MAP_BASE: DivergenceMapResponse = {
  map_id: "map-1",
  segment_id: "seg",
  reviewer_a: "alice",
  reviewer_b: "bob",
  version_a_id: "va",
  version_b_id: "vb",
  generated_at: "2026-05-07T00:00:00Z",
  buckets: [],
  covering_directives: [],
};

describe("CoveringDirectivesBanner integration", () => {
  it("renders the change-in-flight banner when GapReport has covering directives", () => {
    render(
      <GapReportViewer
        data={{ ...GAP_REPORT_BASE, covering_directives: [SAMPLE_DIRECTIVE] }}
      />,
    );
    expect(screen.getByTestId("covering-directives-banner")).toBeTruthy();
    expect(
      screen.getByTestId(`covering-directive-${SAMPLE_DIRECTIVE.directive_id}`),
    ).toBeTruthy();
  });

  it("omits the banner when covering_directives is empty (Gap Report)", () => {
    render(<GapReportViewer data={GAP_REPORT_BASE} />);
    expect(screen.queryByTestId("covering-directives-banner")).toBeNull();
  });

  it("renders the change-in-flight banner on the Divergence Map", () => {
    render(
      <DivergenceMap
        data={{
          ...DIVERGENCE_MAP_BASE,
          covering_directives: [SAMPLE_DIRECTIVE],
        }}
      />,
    );
    expect(screen.getByTestId("covering-directives-banner")).toBeTruthy();
  });
});
