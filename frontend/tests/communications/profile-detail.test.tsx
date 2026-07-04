import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ person_id: "p-1" }),
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

import ProfileDetailPage from "@/app/communications/profiles/[person_id]/page";

const PROFILE = {
  person_id: "p-1",
  profile_version: 1,
  style_signature: {
    sentence_length_band: "short",
    vocabulary_complexity_band: "simple",
    formality_band: "neutral",
    greeting_closing_band: "present",
    hedging_frequency_band: "rare",
    directness_band: "direct",
    response_timing_band: "prompt",
    thread_depth_band: "shallow",
  },
  profile_quality_band: "high",
  created_at: "2026-05-20T00:00:00Z",
};

const EMPTY_CAT = { person_id: "p-1", category: "x", recipients: [] };

describe("ProfileDetailPage", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("renders profile with band cards", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/for-category/")) return mockFetchJson(EMPTY_CAT);
      return mockFetchJson(PROFILE);
    });

    render(<ProfileDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("profile-detail-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("band-cards")).toBeInTheDocument();
  });

  it("renders recipients with shift chips in neutral palette", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/for-category/peer_same_department")) {
        return mockFetchJson({
          person_id: "p-1",
          category: "peer_same_department",
          recipients: [{
            recipient_person_id: "r-1",
            confidence_band: "high",
            style_delta: { formality_shift: "more_formal", directness_shift: null, hedging_shift: null, vocabulary_complexity_shift: null, sentence_length_shift: null, response_timing_shift: null, greeting_override: null, closing_override: null },
          }],
        });
      }
      if (url.includes("/for-category/")) return mockFetchJson(EMPTY_CAT);
      return mockFetchJson(PROFILE);
    });

    render(<ProfileDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("profile-detail-page")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("recipient-row")).toBeInTheDocument();
    });
    const chip = screen.getByTestId("shift-chip");
    expect(chip.className).toContain("bg-slate-200");
    expect(chip.className).toContain("text-slate-700");
  });

  it("mutes low-confidence recipients", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/for-category/external_client")) {
        return mockFetchJson({
          person_id: "p-1",
          category: "external_client",
          recipients: [{ recipient_person_id: "r-2", confidence_band: "low", style_delta: null }],
        });
      }
      if (url.includes("/for-category/")) return mockFetchJson(EMPTY_CAT);
      return mockFetchJson(PROFILE);
    });

    render(<ProfileDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("profile-detail-page")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByTestId("recipient-row")).toBeInTheDocument();
    });
    const row = screen.getByTestId("recipient-row");
    expect(row.className).toContain("opacity-50");
    expect(screen.getByTestId("low-confidence-advisory")).toBeInTheDocument();
  });

  it("renders error state on fetch failure", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network"));
    render(<ProfileDetailPage />);
    await waitFor(() => {
      expect(screen.getByText(/load failed|network/)).toBeInTheDocument();
    });
  });
});
