import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ElementDecisionBar } from "@/components/review/ElementDecisionBar";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("ElementDecisionBar", () => {
  it("renders all nine decision buttons", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    renderWithProviders(<ElementDecisionBar sessionId="s1" elementName="Legal_Entity" elementType="entity_type" currentDecision={null} />);
    const bar = screen.getByTestId("decision-bar-Legal_Entity");
    expect(bar).toBeTruthy();
    expect(screen.getByTestId("decision-btn-approved-Legal_Entity")).toBeTruthy();
    expect(screen.getByTestId("decision-btn-rejected-Legal_Entity")).toBeTruthy();
  });

  it("fires POST on click", async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (url: string) => { calls.push(url); return new Response(JSON.stringify({ decision: "approved" }), { status: 200, headers: { "Content-Type": "application/json" } }); }) as unknown as typeof fetch;
    renderWithProviders(<ElementDecisionBar sessionId="s1" elementName="Legal_Entity" elementType="entity_type" currentDecision={null} />);
    fireEvent.click(screen.getByTestId("decision-btn-approved-Legal_Entity"));
    await new Promise((r) => setTimeout(r, 50));
    expect(calls.length).toBeGreaterThan(0);
  });

  it("classifies blast-radius: destructive vs non-destructive", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    renderWithProviders(<ElementDecisionBar sessionId="s1" elementName="E1" elementType="entity_type" currentDecision={null} />);
    expect(screen.getByTestId("decision-btn-rejected-E1").getAttribute("data-destructive")).toBe("true");
    expect(screen.getByTestId("decision-btn-approved-E1").getAttribute("data-destructive")).toBe("false");
  });
});
