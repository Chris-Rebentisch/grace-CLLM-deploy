import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CQAuthoringForm } from "@/components/cq-canvas/CQAuthoringForm";

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("CQAuthoringForm", () => {
  it("CQ Test Runner integration fires on submit", async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (url: string) => { calls.push(url); return new Response(JSON.stringify({ status: "completed", pass_rate: 1.0 }), { status: 200, headers: { "Content-Type": "application/json" } }); }) as unknown as typeof fetch;
    renderWithProviders(<CQAuthoringForm sessionId="s1" />);
    const input = screen.getByTestId("cq-authoring-input");
    fireEvent.change(input, { target: { value: "What entities exist?" } });
    fireEvent.click(screen.getByTestId("cq-authoring-submit"));
    await new Promise((r) => setTimeout(r, 100));
    expect(calls.length).toBeGreaterThan(0);
  });

  it("renders pass/fail result after validation", async () => {
    globalThis.fetch = (async () => new Response(JSON.stringify({ status: "completed" }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    renderWithProviders(<CQAuthoringForm sessionId="s1" />);
    const input = screen.getByTestId("cq-authoring-input");
    fireEvent.change(input, { target: { value: "Test CQ?" } });
    fireEvent.click(screen.getByTestId("cq-authoring-submit"));
    const result = await screen.findByTestId("cq-authoring-result");
    expect(result).toBeTruthy();
  });
});
