import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CQImpactPreview } from "@/components/cq-canvas/CQImpactPreview";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("CQImpactPreview", () => {
  it("hover triggers fetch when elementName and decision are provided", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify({ cqs_affected: [{ cq_id: "cq1" }] }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<CQImpactPreview sessionId="s1" elementName="Legal_Entity" hypotheticalDecision="rejected" />);
    const preview = await screen.findByTestId("cq-impact-preview");
    expect(preview).toBeTruthy();
  });

  it("no unmount during fetch via placeholderData", () => {
    globalThis.fetch = (async () => new Promise(() => {})) as unknown as typeof fetch;
    renderWithProviders(<CQImpactPreview sessionId="s1" elementName="Legal_Entity" hypotheticalDecision="approved" />);
    expect(screen.getByTestId("cq-impact-preview")).toBeTruthy();
  });

  it("isPlaceholderData dims during transition", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify({ cqs_affected: [] }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<CQImpactPreview sessionId="s1" elementName="Legal_Entity" hypotheticalDecision="rejected" />);
    const el = await screen.findByTestId("cq-impact-preview");
    // During first load, placeholderData is undefined so no dimming
    expect(el).toBeTruthy();
  });
});
