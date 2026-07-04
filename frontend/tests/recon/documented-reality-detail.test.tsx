import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "rpt-1" }),
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

const DR_REPORT = {
  report_id: "rpt-1",
  trigger: "on_demand",
  corpus_below_floor: false,
  aggregations: {
    top_entities: [],
    top_relationships: [],
    legal_entities: [],
    monetary_flow: {},
    participants: [],
    business_activity_signature: {},
    total_vertices: 100,
    total_edges: 50,
  },
  narrative: "Test narrative.",
  generated_at: "2026-05-20T00:00:00Z",
};

const originalFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = originalFetch; });

import DocumentedRealityDetailPage from "@/app/recon/documented-reality/[id]/page";

describe("DocumentedRealityDetailPage", () => {
  it("renders report and evidence-origin action", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchJson(DR_REPORT));

    render(<DocumentedRealityDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("dr-detail-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("documented-reality-report")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-origin-action")).toBeInTheDocument();
    expect(screen.getByTestId("evidence-origin-select")).toBeInTheDocument();
    expect(screen.getByTestId("generate-narrative-btn")).toBeInTheDocument();
  });

  it("generate button triggers API call", async () => {
    const fetchMock = vi.fn().mockResolvedValue(mockFetchJson(DR_REPORT));
    globalThis.fetch = fetchMock;

    render(<DocumentedRealityDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("dr-detail-page")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("evidence-origin-select"), { target: { value: "document" } });
    fireEvent.click(screen.getByTestId("generate-narrative-btn"));

    await waitFor(() => {
      const calls = fetchMock.mock.calls;
      const generateCall = calls.find(
        (c: unknown[]) => typeof c[0] === "string" && c[0].includes("generate"),
      );
      expect(generateCall).toBeDefined();
    });
  });
});
