import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FriendlyTypeCard, humanizeTypeName } from "@/components/review/FriendlyTypeCard";
import type { ReviewElement } from "@/lib/api/types";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

const ELEMENT: ReviewElement = {
  element_type: "entity_type",
  element_name: "Legal_Entity",
  decision: null,
  display_label: "Companies & Organizations",
  plain_description: "The businesses and trusts named in your documents.",
  example_snippet: "Acme Capital Partners, LLC",
  evidence_document_count: 12,
  answerable_questions: ["Who are the parties to each agreement?"],
};

describe("FriendlyTypeCard", () => {
  it("shows plain-language grounding instead of graph jargon", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    renderWithProviders(<FriendlyTypeCard sessionId="s1" element={ELEMENT} />);
    expect(screen.getByText("Companies & Organizations")).toBeTruthy();
    expect(screen.getByText(/businesses and trusts/)).toBeTruthy();
    expect(screen.getByText(/Acme Capital Partners/)).toBeTruthy();
    expect(screen.getByText(/Who are the parties/)).toBeTruthy();
    expect(screen.getByText(/Seen in 12 documents/)).toBeTruthy();
    // The technical name is still present (de-emphasized) for traceability.
    expect(screen.getByText("Legal_Entity")).toBeTruthy();
  });

  it("'Yes, track this' posts an approve decision", async () => {
    let decideBody: unknown = null;
    globalThis.fetch = (async (url: string, opts: RequestInit) => {
      if (typeof url === "string" && url.includes("/decide")) {
        decideBody = JSON.parse(String(opts.body));
        return new Response(JSON.stringify({ decision: {} }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;

    renderWithProviders(<FriendlyTypeCard sessionId="s1" element={ELEMENT} />);
    fireEvent.click(screen.getByTestId("decision-btn-approved-Legal_Entity"));
    await waitFor(() => expect(decideBody).not.toBeNull());
    expect(decideBody).toMatchObject({
      element_type: "entity_type",
      element_name: "Legal_Entity",
      decision: "approved",
    });
  });

  it("falls back to a humanized label when no display_label is given", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    const bare: ReviewElement = {
      element_type: "entity_type",
      element_name: "Insurance_Policy",
      decision: null,
    };
    renderWithProviders(<FriendlyTypeCard sessionId="s1" element={bare} />);
    expect(screen.getByText("Insurance Policy")).toBeTruthy();
  });

  it("opens the assist drawer from 'Something's off?'", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    renderWithProviders(<FriendlyTypeCard sessionId="s1" element={ELEMENT} />);
    fireEvent.click(screen.getByTestId("assist-trigger-Legal_Entity"));
    expect(screen.getByTestId("assist-drawer-Legal_Entity")).toBeTruthy();
  });
});

describe("humanizeTypeName", () => {
  it("turns technical names into readable labels", () => {
    expect(humanizeTypeName("Legal_Entity")).toBe("Legal Entity");
    expect(humanizeTypeName("IntellectualProperty")).toBe("Intellectual Property");
  });
});
