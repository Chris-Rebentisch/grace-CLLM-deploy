import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewPanel } from "@/components/review/ReviewPanel";

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ReviewPanel", () => {
  it("renders the dual-panel shell", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify([{ element_type: "entity_type", element_name: "Legal_Entity", decision: null }]), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<ReviewPanel sessionId="test-session" />);
    const panel = await screen.findByTestId("review-panel");
    expect(panel).toBeTruthy();
  });

  it("shows left and right panels", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify([{ element_type: "entity_type", element_name: "Legal_Entity", decision: null }]), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<ReviewPanel sessionId="test-session" />);
    expect(await screen.findByTestId("review-left-panel")).toBeTruthy();
    expect(screen.getByTestId("review-right-panel")).toBeTruthy();
  });
});
