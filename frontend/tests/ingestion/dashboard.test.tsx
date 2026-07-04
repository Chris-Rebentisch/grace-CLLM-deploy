import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useParams: () => ({}),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

// Mock next/link
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

// Mock recharts (renders nothing in jsdom)
vi.mock("recharts", () => ({
  BarChart: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="mock-bar-chart">{children}</div>
  ),
  Bar: () => null,
  XAxis: () => null,
  YAxis: () => null,
  Tooltip: () => null,
  ResponsiveContainer: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

// Mock telemetry
vi.mock("@/lib/telemetry/emit", () => ({
  postElicitationEvent: vi.fn(),
}));
vi.mock("@/lib/telemetry/events", () => ({
  buildEnvelope: vi.fn(() => ({})),
}));
vi.mock("@/lib/state/session-store", () => ({
  useSessionStore: () => "test-session",
}));

function mockFetchJson(data: unknown) {
  const body = JSON.stringify(data);
  return { ok: true, status: 200, text: () => Promise.resolve(body), json: () => Promise.resolve(data) };
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

import IngestionDashboardPage from "@/app/ingestion/page";

describe("IngestionDashboardPage", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders empty state when no runs or sources", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      mockFetchJson({ items: [], next_cursor: null }),
    );

    render(<IngestionDashboardPage />);
    await waitFor(() => {
      expect(
        screen.getByTestId("ingestion-dashboard-empty"),
      ).toBeInTheDocument();
    });
  });

  it("renders dashboard with sources and runs", async () => {
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount <= 1) {
        return mockFetchJson({
          items: [
            {
              id: "run-1",
              source_id: "src-1",
              status: "completed",
              started_at: "2026-05-20T00:00:00Z",
              triage_tier_counts_json: null,
            },
          ],
          next_cursor: null,
        });
      }
      return mockFetchJson({
        items: [
          {
            id: "src-1",
            name: "Test Source",
            status: "ready",
            source_type: "mbox",
          },
        ],
        next_cursor: null,
      });
    });

    render(<IngestionDashboardPage />);
    await waitFor(() => {
      expect(screen.getByTestId("ingestion-dashboard")).toBeInTheDocument();
    });
    expect(screen.getByTestId("source-badge")).toBeInTheDocument();
    expect(screen.getByTestId("run-row")).toBeInTheDocument();
  });

  it("renders error state when fetch fails", async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error("network error"));

    render(<IngestionDashboardPage />);
    await waitFor(() => {
      expect(
        screen.getByTestId("ingestion-dashboard-error"),
      ).toBeInTheDocument();
    });
  });

  it("renders triage funnel when run has tier counts", async () => {
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount <= 1) {
        return mockFetchJson({
          items: [
            {
              id: "run-1",
              source_id: "src-1",
              status: "completed",
              started_at: "2026-05-20T00:00:00Z",
              triage_tier_counts_json: {
                total_processed: 100,
                tier1_filtered: 50,
                tier2_filtered: 20,
                tier3_filtered: 10,
                tier3_passed: 10,
                tier4_filtered: 5,
                tier4_passed: 5,
              },
            },
          ],
          next_cursor: null,
        });
      }
      return mockFetchJson({
        items: [
          { id: "src-1", name: "Source", status: "ready", source_type: "mbox" },
        ],
        next_cursor: null,
      });
    });

    render(<IngestionDashboardPage />);
    await waitFor(() => {
      expect(screen.getByTestId("ingestion-dashboard")).toBeInTheDocument();
    });
    expect(screen.getByTestId("funnel-band-label")).toBeInTheDocument();
  });

  it("shows re-consent hint for OAuth source in error", async () => {
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      callCount++;
      if (callCount <= 1) {
        return mockFetchJson({
          items: [
            {
              id: "run-1",
              source_id: "src-1",
              status: "completed",
              started_at: "2026-05-20T00:00:00Z",
              triage_tier_counts_json: null,
            },
          ],
          next_cursor: null,
        });
      }
      return mockFetchJson({
        items: [
          {
            id: "src-1",
            name: "Gmail Source",
            status: "error",
            source_type: "gmail",
          },
        ],
        next_cursor: null,
      });
    });

    render(<IngestionDashboardPage />);
    await waitFor(() => {
      expect(screen.getByTestId("ingestion-dashboard")).toBeInTheDocument();
    });
    expect(screen.getByText(/re-consent needed/)).toBeInTheDocument();
  });
});
