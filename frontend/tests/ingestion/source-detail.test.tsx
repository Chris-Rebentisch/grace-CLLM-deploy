import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ source_id: "src-1" }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...rest
  }: {
    children: React.ReactNode;
    href: string;
    [k: string]: unknown;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
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

import SourceDetailPage from "@/app/ingestion/[source_id]/page";

describe("SourceDetailPage", () => {
  beforeEach(() => { vi.restoreAllMocks(); });

  it("renders source detail when data loads", async () => {
    globalThis.fetch = vi.fn().mockImplementation(async (url: string) => {
      if (url.includes("/status")) {
        return mockFetchJson({ source_id: "src-1", status: "ready", last_run_at: "2026-05-20T00:00:00Z" });
      }
      if (url.includes("/events")) {
        return mockFetchJson({
          items: [{ event_id: "ev-1", sender_email: "a@t.com", subject: "Hi", sent_at: "2026-05-20", triage_tier_outcome: "passed" }],
          next_cursor: null,
        });
      }
      return mockFetchJson({ id: "src-1", name: "Test Source", status: "ready", source_type: "mbox" });
    });

    render(<SourceDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("source-detail")).toBeInTheDocument();
    });
  });

  it("renders error state on fetch failure", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network"));
    render(<SourceDetailPage />);
    await waitFor(() => {
      expect(screen.getByText(/load failed|network/)).toBeInTheDocument();
    });
  });
});
