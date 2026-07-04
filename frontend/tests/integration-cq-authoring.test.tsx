import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { CQAuthoringForm } from "@/components/cq-canvas/CQAuthoringForm";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>{ui}</QueryClientProvider>,
  );
}

describe("integration: CQ authoring", () => {
  it("open canvas -> author CQ -> CQ Test Runner result renders", async () => {
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({ status: "completed", pass_rate: 1.0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )) as unknown as typeof fetch;

    renderWithProviders(<CQAuthoringForm sessionId="test-session-1" />);

    const input = screen.getByTestId("cq-authoring-input");
    fireEvent.change(input, {
      target: { value: "Does the ontology cover insurance policies?" },
    });
    fireEvent.click(screen.getByTestId("cq-authoring-submit"));

    const result = await screen.findByTestId("cq-authoring-result");
    expect(result).toBeTruthy();
    expect(result.textContent).toContain("PASS");
  });
});
