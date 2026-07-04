// Tests for `frontend/components/recon/DivergenceMap.tsx` (Chunk 37, D284 / D288).

import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DivergenceMap } from "@/components/recon/DivergenceMap";
import type { DivergenceMapResponse } from "@/lib/api/recon-types";

const FIXTURE: DivergenceMapResponse = {
  map_id: "map-1",
  segment_id: "seg37",
  reviewer_a: "alice",
  reviewer_b: "bob",
  version_a_id: "va",
  version_b_id: "vb",
  generated_at: "2026-05-07T00:00:00Z",
  covering_directives: [],
  buckets: [
    {
      bucket_name: "additive_A",
      entries: [{ element_name: "Trust", element_type: "entity", instance_count: 4 }],
    },
    {
      bucket_name: "additive_B",
      entries: [{ element_name: "Account", element_type: "entity", instance_count: 7 }],
    },
    {
      bucket_name: "consensus",
      entries: [{ element_name: "Company", element_type: "entity", instance_count: 12 }],
    },
    {
      bucket_name: "contradictory",
      entries: [
        {
          element_name: "Beneficiary",
          element_type: "entity",
          instance_count: 2,
        },
      ],
    },
  ],
};

describe("DivergenceMap (Chunk 37, D284 / D288)", () => {
  it("renders the three-column desktop layout with each bucket's entries", () => {
    render(<DivergenceMap data={FIXTURE} />);
    expect(screen.getByTestId("divergence-map-grid")).toBeTruthy();
    expect(screen.getByTestId("divergence-map-column-a").textContent).toContain(
      "Trust",
    );
    expect(
      screen.getByTestId("divergence-map-column-consensus").textContent,
    ).toContain("Company");
    expect(screen.getByTestId("divergence-map-column-b").textContent).toContain(
      "Account",
    );
  });

  it("opens the evidence drawer when an evidence-count badge is clicked", () => {
    render(<DivergenceMap data={FIXTURE} />);
    const badges = screen.getAllByTestId("divergence-map-evidence-badge");
    expect(badges.length).toBeGreaterThan(0);
    fireEvent.click(badges[0]!);
    expect(screen.getByTestId("divergence-map-drawer")).toBeTruthy();
  });

  it("renders the tabbed fallback for narrow viewports", () => {
    render(<DivergenceMap data={FIXTURE} />);
    expect(screen.getByTestId("divergence-map-tabs")).toBeTruthy();
    expect(screen.getAllByRole("tab").length).toBe(3);
  });
});
