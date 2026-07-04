import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({}),
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

import ProfileListPage from "@/app/communications/profiles/page";

describe("ProfileListPage", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("renders profile list with items", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockFetchJson({
        items: [{ person_id: "p-1", profile_version: 1, style_signature: null, profile_quality_band: "high", created_at: "2026-05-20T00:00:00Z" }],
        next_cursor: null,
      }),
    );

    render(<ProfileListPage />);
    await waitFor(() => {
      expect(screen.getByTestId("profile-list-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("profile-row")).toBeInTheDocument();
  });

  it("renders empty state when no profiles", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockFetchJson({ items: [], next_cursor: null }),
    );

    render(<ProfileListPage />);
    await waitFor(() => {
      expect(screen.getByTestId("profile-list-empty")).toBeInTheDocument();
    });
  });
});
