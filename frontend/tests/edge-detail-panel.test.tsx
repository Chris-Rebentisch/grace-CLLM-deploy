import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EdgeDetailPanel } from "@/components/graph/EdgeDetailPanel";
import type { RelationshipRecord } from "@/lib/api/types";

const SAMPLE: RelationshipRecord = {
  grace_id: "edge-uuid-abcd",
  relationship_type: "owns",
  source_grace_id: "src-1",
  target_grace_id: "tgt-1",
  properties: {
    as_of: "2026-04-01",
    // D217-forbidden numerals
    extraction_confidence: 0.75,
    relationship_confidence: 0.66,
  },
  source_document_id: "doc-77",
  extraction_event_id: "evt-101",
  ontology_module: "legal_entity",
  human_validated: false,
  extraction_confidence: 0.75,
};

describe("EdgeDetailPanel", () => {
  it("renders endpoints, properties, and evidence chain without forbidden numerals", () => {
    render(<EdgeDetailPanel edge={SAMPLE} />);
    const endpoints = screen.getByTestId("edge-endpoints").textContent ?? "";
    expect(endpoints).toContain("src-1");
    expect(endpoints).toContain("tgt-1");
    // Provenance
    expect(screen.getByTestId("edge-provenance").textContent).toContain(
      "doc-77",
    );
    expect(screen.getByTestId("edge-provenance").textContent).toContain(
      "evt-101",
    );
    // Forbidden numerals gone
    const props = screen.getByTestId("edge-properties").textContent ?? "";
    expect(props).not.toMatch(/0\.75/);
    expect(props).not.toMatch(/0\.66/);
    expect(props).not.toMatch(/extraction_confidence/);
    expect(props).not.toMatch(/relationship_confidence/);
    // Domain property still there
    expect(props).toMatch(/2026-04-01/);
    // Human-validated pending state
    expect(screen.getByTestId("edge-human-validated-badge").textContent).toBe(
      "— pending",
    );
  });

  it("renders empty marker when edge is null", () => {
    render(<EdgeDetailPanel edge={null} />);
    expect(screen.queryByTestId("edge-detail-panel")).toBeNull();
  });
});
