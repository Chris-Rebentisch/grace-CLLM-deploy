import { describe, expect, it, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewAssistDrawer } from "@/components/review/ReviewAssistDrawer";
import type { ReviewElement } from "@/lib/api/types";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
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
};

describe("ReviewAssistDrawer", () => {
  it("sends a message, shows the reply, and confirms a rename decision", async () => {
    let decideBody: unknown = null;
    globalThis.fetch = (async (url: string, opts: RequestInit) => {
      if (typeof url === "string" && url.includes("/assist")) {
        return new Response(
          JSON.stringify({
            reply: "Sure — I can rename it to whatever you call them.",
            suggested_action: {
              action: "rename",
              button_label: "Rename to 'Clients'",
              rationale: "You call these Clients.",
              new_name: "Clients",
              merge_with: null,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (typeof url === "string" && url.includes("/decide")) {
        decideBody = JSON.parse(String(opts.body));
        return new Response(JSON.stringify({ decision: {} }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }) as unknown as typeof fetch;

    renderWithProviders(
      <ReviewAssistDrawer
        sessionId="s1"
        element={ELEMENT}
        elementTypeForApi="entity_type"
        friendlyLabel="Companies & Organizations"
        open={true}
        onOpenChange={() => {}}
      />,
    );

    const textarea = screen.getByRole("textbox");
    fireEvent.change(textarea, { target: { value: "I call these Clients" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    // Assistant reply renders.
    await waitFor(() =>
      expect(screen.getByText(/I can rename it/)).toBeTruthy(),
    );

    // The suggested action surfaces a confirm button.
    const confirm = await screen.findByTestId("assist-confirm-Legal_Entity");
    fireEvent.click(confirm);

    await waitFor(() => expect(decideBody).not.toBeNull());
    expect(decideBody).toMatchObject({
      element_type: "entity_type",
      element_name: "Legal_Entity",
      decision: "renamed",
      modified_data: { name: "Clients" },
    });
  });
});
