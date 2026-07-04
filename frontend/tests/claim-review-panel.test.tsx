import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClaimReviewPanel } from "@/components/claims/ClaimReviewPanel";
import type { ClaimRecord } from "@/lib/api/types";

function sampleClaim(): ClaimRecord {
  return {
    claim_id: "claim-1",
    extraction_event_id: "event-1",
    entity_type: "Legal_Entity",
    relationship_type: null,
    subject_name: "Acme Corp",
    predicate: "is a",
    object_name: "Legal_Entity",
    evidence_spans: [
      { text: "Acme Corp is a Delaware corporation.", start_char: 0, end_char: 35 },
    ],
    status: "quarantined",
    verdict: "refuted",
    decision_source: "verifier",
    human_decided_at: null,
    ontology_module: "core",
    source_document_id: "doc-1",
    constraint_violations: null,
    verifier_contradiction_reason: "evidence weak",
    supersedes_claim_id: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

describe("ClaimReviewPanel", () => {
  it("evidence-first ordering: summary → evidence → rationale → teach-back-gate → ... → disposition", () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <ClaimReviewPanel claim={sampleClaim()} />
      </QueryClientProvider>,
    );
    const panel = screen.getByTestId("claim-review-panel");
    const order = [
      "claim-summary",
      "evidence-text-panel",
      "quarantine-rationale",
      "teach-back-gate",
      "verifier-note-panel",
      "disposition-bar",
    ];
    const positions = order.map((id) => {
      const el = panel.querySelector(`[data-testid="${id}"]`);
      return el ? Array.from(panel.querySelectorAll("*")).indexOf(el) : -1;
    });
    for (let i = 1; i < positions.length; i++) {
      expect(positions[i]).toBeGreaterThan(positions[i - 1]);
    }
  });
});
