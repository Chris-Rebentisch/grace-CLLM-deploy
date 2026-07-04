import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { NodeDetailPanel } from "@/components/graph/NodeDetailPanel";
import type { EntityRecord } from "@/lib/api/types";

const originalFetch = globalThis.fetch;

function withClient(ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>{ui}</QueryClientProvider>,
  );
}

const SAMPLE: EntityRecord = {
  grace_id: "uuid-1234-abcd",
  entity_type: "Legal_Entity",
  properties: {
    name: "Acme Capital",
    jurisdiction: "Delaware",
    // D217-forbidden numerals — must NOT render as numerals
    extraction_confidence: 0.87,
    rrf_score: 0.91,
  },
  source_document_id: "doc-42",
  extraction_event_id: "evt-99",
  ontology_module: "legal_entity",
  human_validated: true,
  valid_from: null,
  valid_to: null,
  extraction_confidence: 0.87,
};

describe("NodeDetailPanel", () => {
  it("renders properties, provenance, and human-validated badge without numeric confidence", () => {
    globalThis.fetch = vi.fn(async () =>
      new Response(
        JSON.stringify({ seed: {}, neighbors: [], edges: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ) as unknown as typeof fetch;
    try {
      withClient(<NodeDetailPanel entity={SAMPLE} />);

      // Grace id rendered
      expect(screen.getByTestId("node-grace-id").textContent).toContain(
        "uuid-1234-abcd",
      );
      // Human-validated appears as checkmark + text, not as "1.0" or "true"
      expect(screen.getByTestId("human-validated-badge").textContent).toBe(
        "✓ validated",
      );
      // Provenance ids present
      expect(screen.getByTestId("node-provenance").textContent).toContain(
        "doc-42",
      );
      expect(screen.getByTestId("node-provenance").textContent).toContain(
        "evt-99",
      );
      // The forbidden numerals are filtered out of the property list
      const props = screen.getByTestId("node-properties").textContent ?? "";
      expect(props).not.toMatch(/0\.87/);
      expect(props).not.toMatch(/0\.91/);
      expect(props).not.toMatch(/extraction_confidence/);
      expect(props).not.toMatch(/rrf_score/);
      // Domain properties still show
      expect(props).toMatch(/Acme Capital/);
      expect(props).toMatch(/Delaware/);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("renders no panel when entity is null", () => {
    withClient(<NodeDetailPanel entity={null} />);
    expect(screen.queryByTestId("node-detail-panel")).toBeNull();
  });
});
