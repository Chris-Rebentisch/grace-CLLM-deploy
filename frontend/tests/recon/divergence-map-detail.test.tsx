import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "map-1" }),
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

import DivergenceMapDetailPage from "@/app/recon/divergence-maps/[id]/page";

describe("DivergenceMapDetailPage", () => {
  it("renders divergence map with source-origins badges", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(mockFetchJson({
      map_id: "map-1",
      segment_id: null,
      reviewer_a: "Alice",
      reviewer_b: "Bob",
      version_a_id: "v-1",
      version_b_id: "v-2",
      generated_at: "2026-05-20T00:00:00Z",
      covering_directives: [],
      buckets: [
        { bucket_name: "additive_A", entries: [{ element_name: "TestEntity", element_type: "Entity", instance_count: 3, source_origins: ["document"] }] },
        { bucket_name: "additive_B", entries: [] },
        { bucket_name: "consensus", entries: [] },
        { bucket_name: "contradictory", entries: [] },
      ],
    }));

    render(<DivergenceMapDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("divergence-map-detail-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("divergence-map")).toBeInTheDocument();
  });
});
