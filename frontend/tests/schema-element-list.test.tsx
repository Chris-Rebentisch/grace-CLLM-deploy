import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SchemaElementList } from "@/components/review/SchemaElementList";

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

describe("SchemaElementList", () => {
  it("renders entity and relationship type lists", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    const elements = [
      { element_type: "entity_type", element_name: "Legal_Entity", decision: null },
      { element_type: "relationship_type", element_name: "owns", decision: null },
    ];
    renderWithProviders(<SchemaElementList sessionId="s1" elements={elements} />);
    expect(screen.getByTestId("schema-element-list")).toBeTruthy();
    expect(screen.getByText("Legal_Entity")).toBeTruthy();
    expect(screen.getByText("owns")).toBeTruthy();
  });

  it("renders hierarchy tree with entity types and relationships", () => {
    globalThis.fetch = (async () => new Response("{}")) as unknown as typeof fetch;
    const elements = [
      { element_type: "entity_type", element_name: "Company", decision: "approved" },
    ];
    renderWithProviders(<SchemaElementList sessionId="s1" elements={elements} />);
    expect(screen.getByText("Entity Types")).toBeTruthy();
    expect(screen.getByText("Relationship Types")).toBeTruthy();
  });
});
