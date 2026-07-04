import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ClaimList } from "@/components/claims/ClaimList";

const sampleResponse = {
  items: [
    {
      claim_id: "claim-1",
      extraction_event_id: "event-1",
      entity_type: "Legal_Entity",
      relationship_type: null,
      subject_name: "Acme Corp",
      predicate: "is a",
      object_name: null,
      evidence_spans: [{ text: "Acme Corp", start_char: 0, end_char: 9 }],
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
    },
  ],
  next_cursor: "abc",
  total_count: 2,
};

let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(sampleResponse), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("ClaimList", () => {
  it("renders paged list with filter chips and a row per claim", async () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={qc}>
        <ClaimList onSelect={() => {}} selectedClaimId={null} />
      </QueryClientProvider>,
    );
    expect(await screen.findByTestId("claim-list-filters")).toBeTruthy();
    expect(await screen.findByTestId("claim-row-claim-1")).toBeTruthy();
    expect(screen.getByTestId("filter-status")).toBeTruthy();
    expect(screen.getByTestId("filter-verdict")).toBeTruthy();
    expect(screen.getByTestId("claim-list-next")).toBeTruthy();
  });
});
