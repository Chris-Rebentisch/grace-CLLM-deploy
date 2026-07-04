import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReviewProgressBar } from "@/components/review/ReviewProgressBar";

describe("ReviewProgressBar", () => {
  it("renders counts (allowed numerals)", () => {
    globalThis.fetch = (async () => new Response(JSON.stringify({ total_elements: 10, reviewed_elements: 3 }), { status: 200, headers: { "Content-Type": "application/json" } })) as unknown as typeof fetch;
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><ReviewProgressBar sessionId="s1" /></QueryClientProvider>);
    expect(screen.getByTestId("review-progress-bar")).toBeTruthy();
  });
});
