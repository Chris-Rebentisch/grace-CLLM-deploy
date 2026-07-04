// Chunk 41 D322/D324/D325 — Decomposition detail page integration tests.

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";

vi.mock("next/navigation", () => ({
  useParams: () => ({ run_id: "00000000-0000-0000-0000-000000000099" }),
}));

import DecompositionDetailPage from "@/app/decomposition/[run_id]/page";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function makeFetchRouter(byPath: Array<[RegExp, unknown, number?]>) {
  return (async (url: string, init: RequestInit = {}) => {
    void init;
    for (const [pattern, body, status] of byPath) {
      if (pattern.test(url)) {
        return new Response(JSON.stringify(body), {
          status: status ?? 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

const RUN_BASE = {
  run_id: "00000000-0000-0000-0000-000000000099",
  archive_root: "/tmp/x",
  archive_root_canonical_hash: "h",
  triggered_at: "2026-05-08T00:00:00Z",
  completed_at: null,
};

describe("DecompositionDetailPage (Chunk 41)", () => {
  it("renders the LowStabilityBadge when low_stability_flag is true", async () => {
    globalThis.fetch = makeFetchRouter([
      [
        /\/api\/decomposition\/runs\/[^/]+$/,
        {
          ...RUN_BASE,
          status: "paused_pre_layer5",
          layer3_payload: { low_stability_flag: true },
          layer4_payload: { hypotheses: [] },
        },
      ],
    ]);
    render(<DecompositionDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("low-stability-badge")).toBeTruthy();
    });
  });

  it("hides the LowStabilityBadge when flag is false", async () => {
    globalThis.fetch = makeFetchRouter([
      [
        /\/api\/decomposition\/runs\/[^/]+$/,
        {
          ...RUN_BASE,
          status: "paused_pre_layer5",
          layer3_payload: { low_stability_flag: false },
          layer4_payload: { hypotheses: [] },
        },
      ],
    ]);
    render(<DecompositionDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("decomposition-detail-status").textContent).toBe(
        "paused_pre_layer5",
      );
    });
    expect(screen.queryByTestId("low-stability-badge")).toBeNull();
  });

  it("submits a Layer 5 accepted_segmented decision via POST", async () => {
    const calls: Array<{ url: string; method: string }> = [];
    globalThis.fetch = (async (url: string, init: RequestInit = {}) => {
      calls.push({ url, method: (init.method ?? "GET").toUpperCase() });
      if (url.endsWith(`/api/decomposition/runs/00000000-0000-0000-0000-000000000099`)) {
        return new Response(
          JSON.stringify({
            ...RUN_BASE,
            status: "paused_pre_layer5",
            layer3_payload: {},
            layer4_payload: { hypotheses: [] },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    render(<DecompositionDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("hypothesis-decision-bar")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("decision-accepted-segmented"));
    await waitFor(() => {
      const post = calls.find(
        (c) => c.method === "POST" && c.url.endsWith("/layer5/decision"),
      );
      expect(post).toBeTruthy();
    });
  });

  it("renders Layer 6 segment buttons from layer4 hypotheses", async () => {
    globalThis.fetch = makeFetchRouter([
      [
        /\/api\/decomposition\/runs\/[^/]+$/,
        {
          ...RUN_BASE,
          status: "paused_pre_layer6",
          layer3_payload: {},
          layer4_payload: {
            hypotheses: [
              {
                proposed_segments: [{ name: "finance" }, { name: "delivery" }],
              },
            ],
          },
        },
      ],
    ]);
    render(<DecompositionDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("generate-sample-cqs-finance")).toBeTruthy();
      expect(screen.getByTestId("generate-sample-cqs-delivery")).toBeTruthy();
    });
  });

  it("opens the ratification dialog with a YAML preview", async () => {
    globalThis.fetch = makeFetchRouter([
      [
        /\/api\/decomposition\/runs\/[^/]+$/,
        {
          ...RUN_BASE,
          status: "paused_pre_layer7",
          layer3_payload: {},
          layer4_payload: {
            hypotheses: [{ proposed_segments: [{ name: "finance" }] }],
          },
        },
      ],
    ]);
    render(<DecompositionDetailPage />);
    await waitFor(() =>
      expect(screen.getByTestId("open-ratify-dialog")).toBeTruthy(),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("open-ratify-dialog"));
    });
    expect(screen.getByTestId("segmentation-map-ratify-dialog")).toBeTruthy();
    expect(
      screen.getByTestId("segmentation-map-yaml-preview").textContent ?? "",
    ).toContain("schema_version");
  });
});
