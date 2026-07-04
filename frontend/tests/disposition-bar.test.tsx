import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DispositionBar } from "@/components/claims/DispositionBar";
import { useClaimReviewStore } from "@/lib/state/claim-review-store";
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
      { text: "span A", start_char: 0, end_char: 6 },
      { text: "span B", start_char: 7, end_char: 13 },
    ],
    status: "quarantined",
    verdict: "refuted",
    decision_source: "verifier",
    human_decided_at: null,
    ontology_module: "core",
    source_document_id: "doc-1",
    constraint_violations: null,
    verifier_contradiction_reason: null,
    supersedes_claim_id: null,
    created_at: "2026-05-01T00:00:00Z",
  };
}

beforeEach(() => {
  // Reset Zustand store between tests.
  useClaimReviewStore.setState({
    activeClaim: sampleClaim(),
    teachBackLabels: {},
    teachBackCorrections: {},
    editDraft: null,
    editFormOpen: false,
    reviewer: "alice",
  });
});

describe("DispositionBar", () => {
  it("Teach-Back gating: buttons disabled until every span has a label", () => {
    const qc = new QueryClient();
    render(
      <QueryClientProvider client={qc}>
        <DispositionBar claim={sampleClaim()} />
      </QueryClientProvider>,
    );
    const accept = screen.getByTestId("disposition-accept") as HTMLButtonElement;
    const reject = screen.getByTestId("disposition-reject") as HTMLButtonElement;
    const edit = screen.getByTestId("disposition-edit-and-accept") as HTMLButtonElement;
    expect(accept.disabled).toBe(true);
    expect(reject.disabled).toBe(true);
    expect(edit.disabled).toBe(true);

    // Label only one span — gate should remain closed.
    useClaimReviewStore.getState().setTeachBackLabel(0, "correct");
    fireEvent.change(screen.getByTestId("disposition-bar")); // force rerender
    expect((screen.getByTestId("disposition-accept") as HTMLButtonElement).disabled).toBe(true);

    // Label all spans — gate opens.
    useClaimReviewStore.getState().setTeachBackLabel(1, "wrong");
    // Re-render via store update; triggers React state change in subscription.
    expect(useClaimReviewStore.getState().isTeachBackComplete()).toBe(true);
  });
});
