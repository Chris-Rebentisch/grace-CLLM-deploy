import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ segment: "finance" }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: { children: React.ReactNode; href: string; [k: string]: unknown }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));
vi.mock("@/lib/telemetry/emit", () => ({ postElicitationEvent: vi.fn() }));
vi.mock("@/lib/telemetry/events", () => ({ buildEnvelope: vi.fn(() => ({})) }));
vi.mock("@/lib/state/session-store", () => ({ useSessionStore: () => "test-session" }));

function mockFetchJson(data: unknown) {
  const body = JSON.stringify(data);
  return { ok: true, status: 200, text: () => Promise.resolve(body), json: () => Promise.resolve(data) };
}

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

import AggregateProfilePage from "@/app/communications/profiles/aggregate/[segment]/page";

describe("AggregateProfilePage", () => {
  it("renders aggregate profile data", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockFetchJson({
        aggregate_segment: "finance",
        profile_count: 5,
        avg_sentence_length_band: "medium",
        avg_formality_band: "formal",
        avg_directness_band: "indirect",
      }),
    );

    render(<AggregateProfilePage />);
    await waitFor(() => {
      expect(screen.getByTestId("aggregate-profile-page")).toBeInTheDocument();
    });
  });

  it("renders error when fetch fails", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network"));
    render(<AggregateProfilePage />);
    await waitFor(() => {
      expect(screen.getByTestId("aggregate-error")).toBeInTheDocument();
    });
  });
});
