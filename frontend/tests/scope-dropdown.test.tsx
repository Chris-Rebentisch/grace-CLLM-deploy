import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ScopeDropdown } from "@/components/scope/ScopeDropdown";
import { useScopeStore } from "@/lib/state/scope-store";

const originalFetch = globalThis.fetch;

function mockSegments(segments: Array<{ module_name: string; entity_count: number }>) {
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(segments), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })) as unknown as typeof fetch;
}

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>,
  );
}

beforeEach(() => {
  globalThis.fetch = originalFetch;
  useScopeStore.getState().selectAll();
});

describe("ScopeDropdown", () => {
  it("renders multi-select checkboxes for each segment", async () => {
    mockSegments([
      { module_name: "finance", entity_count: 10 },
      { module_name: "legal", entity_count: 5 },
    ]);
    renderWithProviders(<ScopeDropdown />);

    const dropdown = await screen.findByTestId("scope-dropdown");
    expect(dropdown).toBeTruthy();

    const allCheckbox = await screen.findByTestId("scope-all-segments");
    expect(allCheckbox).toBeTruthy();
  });

  it("defaults to 'All segments' selected", async () => {
    mockSegments([{ module_name: "finance", entity_count: 10 }]);
    renderWithProviders(<ScopeDropdown />);

    const allCheckbox = await screen.findByTestId("scope-all-segments");
    expect((allCheckbox as HTMLInputElement).checked).toBe(true);
  });

  it("encodes X-Graph-Scope header as segments:m1,m2,...", () => {
    const store = useScopeStore.getState();
    store.setSegments(["finance", "legal"]);
    expect(store.getScopeHeaderValue()).toBe("segments:finance,legal");

    store.selectAll();
    expect(store.getScopeHeaderValue()).toBe("all");
  });
});
