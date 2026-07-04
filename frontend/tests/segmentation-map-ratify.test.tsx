// Chunk 41 D325 — SegmentationMapRatifyDialog: YAML preview + confirm POST.

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

import { SegmentationMapRatifyDialog } from "@/components/decomposition/SegmentationMapRatifyDialog";
import type { SegmentationMap } from "@/lib/api/types";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

const SM: SegmentationMap = {
  map_id: "00000000-0000-0000-0000-000000000000",
  decomposition_run_id: "11111111-1111-1111-1111-111111111111",
  schema_version: "1.0",
  payload_hash: "pending",
  previous_hash: null,
  payload: {
    segments: [{ name: "finance" }, { name: "delivery" }],
  },
  null_hypothesis_accepted: false,
  created_at: "2026-05-08T00:00:00Z",
};

describe("SegmentationMapRatifyDialog (Chunk 41 D325)", () => {
  it("renders the YAML preview when open", () => {
    render(
      <SegmentationMapRatifyDialog
        open={true}
        onClose={() => {}}
        runId="11111111-1111-1111-1111-111111111111"
        segmentationMap={SM}
      />,
    );
    const pre = screen.getByTestId("segmentation-map-yaml-preview");
    const text = pre.textContent ?? "";
    expect(text).toContain("schema_version");
    expect(text).toContain('"1.0"');
    expect(text).toContain("null_hypothesis_accepted");
    expect(text).toContain("segments");
  });

  it("POSTs to ratify endpoint on confirm", async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (url: string) => {
      calls.push(url);
      return new Response(
        JSON.stringify({
          segmentation_map_id: "abc",
          payload_hash: "deadbeef",
          previous_hash: null,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    }) as unknown as typeof fetch;

    const onClose = vi.fn();
    render(
      <SegmentationMapRatifyDialog
        open={true}
        onClose={onClose}
        runId="11111111-1111-1111-1111-111111111111"
        segmentationMap={SM}
      />,
    );
    fireEvent.click(screen.getByTestId("segmentation-map-ratify-confirm"));
    await waitFor(() => {
      expect(calls.length).toBeGreaterThan(0);
    });
    expect(calls[0]).toMatch(
      /\/api\/decomposition\/runs\/11111111-1111-1111-1111-111111111111\/segmentation-map\/ratify$/,
    );
    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });
});
