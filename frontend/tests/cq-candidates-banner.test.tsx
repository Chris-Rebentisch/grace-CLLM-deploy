import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CQCandidatesBanner } from "@/components/cq-canvas/CQCandidatesBanner";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("CQCandidatesBanner", () => {
  it("polls and renders candidates", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify([{ id: "c1", cq_text: "Test CQ?", source_origin: "local_documents", validation_status: "quarantined" }]), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<CQCandidatesBanner sessionId="s1" />);
    const banner = await screen.findByTestId("cq-candidates-banner");
    expect(banner).toBeTruthy();
  });

  it("renders quarantine badges with provenance", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify([{ id: "c1", cq_text: "Test?", source_origin: "ontology_seed", validation_status: "quarantined" }]), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<CQCandidatesBanner sessionId="s1" />);
    const source = await screen.findByTestId("candidate-source-c1");
    expect(source.textContent).toBe("ontology_seed");
  });
});
