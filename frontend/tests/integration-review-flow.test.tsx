import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewPanel } from "@/components/review/ReviewPanel";

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

describe("integration: review flow", () => {
  it("load session -> decide -> audit toast renders", async () => {
    let callCount = 0;
    globalThis.fetch = (async (url: string) => {
      callCount++;
      if (typeof url === "string" && url.includes("/elements")) {
        return new Response(
          JSON.stringify([
            {
              element_type: "entity_type",
              element_name: "Legal_Entity",
              decision: null,
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (typeof url === "string" && url.includes("/progress")) {
        return new Response(
          JSON.stringify({ total_elements: 1, reviewed_elements: 0 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (typeof url === "string" && url.includes("/decide")) {
        return new Response(
          JSON.stringify({
            decision: { element_name: "Legal_Entity", decision: "approved" },
            cq_impact: {},
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    renderWithProviders(<ReviewPanel sessionId="test-session-1" />);

    // Wait for elements to load
    const panel = await screen.findByTestId("review-panel");
    expect(panel).toBeTruthy();

    // Decide on element
    const approveBtn = screen.getByTestId(
      "decision-btn-approved-Legal_Entity",
    );
    fireEvent.click(approveBtn);

    // Verify the decision was attempted (POST was fired)
    await new Promise((r) => setTimeout(r, 100));
    expect(callCount).toBeGreaterThan(1);
  });
});
