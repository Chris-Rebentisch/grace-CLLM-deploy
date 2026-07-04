import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "session-1" }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/telemetry/emit", () => ({ postElicitationEvent: vi.fn() }));
vi.mock("@/lib/telemetry/events", () => ({ buildEnvelope: vi.fn(() => ({})) }));
vi.mock("@/lib/state/session-store", () => ({ useSessionStore: () => "test-session" }));
vi.mock("@/lib/telemetry/bus", () => ({ emitTelemetry: vi.fn() }));

function mockFetchJson(data: unknown) {
  const body = JSON.stringify(data);
  return { ok: true, status: 200, text: () => Promise.resolve(body), json: () => Promise.resolve(data) };
}

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

import GapReportDetailPage from "@/app/recon/gap-reports/[id]/page";

const GAP_REPORT = {
  session_id: "session-1",
  reviewer: "alice",
  generated_at: "2026-05-20T00:00:00Z",
  evidence_grounding_score: null,
  evidence_grounding_threshold: 0.5,
  graph_population_floor_breach: null,
  emphasized_with_evidence: [],
  emphasized_without_evidence: [],
  unemphasized_in_evidence: [],
  covering_directives: [],
};

describe("GapReportDetailPage", () => {
  it("renders gap report viewer when data loads", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchJson(GAP_REPORT));

    render(<GapReportDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("gap-report-viewer")).toBeInTheDocument();
    });
  });

  it("renders source-type filter chips", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchJson(GAP_REPORT));

    render(<GapReportDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("source-type-filter")).toBeInTheDocument();
    });
    const buttons = screen.getByTestId("source-type-filter").querySelectorAll("button");
    expect(buttons.length).toBe(4);
  });
});
