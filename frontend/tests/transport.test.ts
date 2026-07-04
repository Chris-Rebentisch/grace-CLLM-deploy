import { afterEach, describe, expect, it, vi } from "vitest";
import {
  BlockingFetchTransport,
  getChatTransport,
  setChatTransport,
  type ChatTransport,
} from "@/lib/api/transport";
import type {
  CloseConfirmRequest,
  CloseSummaryRequest,
  RegenerationQuery,
  RegenerationResponse,
} from "@/lib/api/types";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  setChatTransport(null);
  vi.restoreAllMocks();
});

describe("BlockingFetchTransport", () => {
  it("routes sendQuery/sendCloseSummary/sendCloseConfirm to the correct endpoints", async () => {
    const captured: Array<{ url: string; body: unknown }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      captured.push({ url, body: init?.body ? JSON.parse(init.body as string) : null });
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    const transport = new BlockingFetchTransport();
    const query: RegenerationQuery = {
      query_text: "hi",
      retrieval_query: null,
      phase_state: "open",
      overrides: null,
    };
    await transport.sendQuery(query).catch(() => undefined);

    const closeSummary: CloseSummaryRequest = {
      session_id: "00000000-0000-0000-0000-000000000001",
      phase_state: "close",
      messages: [],
      phase_durations_ms: {},
    };
    await transport.sendCloseSummary(closeSummary).catch(() => undefined);

    const closeConfirm: CloseConfirmRequest = {
      session_id: "00000000-0000-0000-0000-000000000001",
      final_summary: {
        narrative: "x",
        ontology_changes: [],
        cqs_flipped_state: [],
        decisions_recorded: [],
        deferred_items: [],
        certainty_band_shifts: [],
      },
      summary_edited: false,
      summary_rejected: false,
    };
    await transport.sendCloseConfirm(closeConfirm).catch(() => undefined);

    expect(captured.map((c) => c.url)).toEqual([
      expect.stringContaining("/api/regeneration/query"),
      expect.stringContaining("/api/regeneration/close-summary"),
      expect.stringContaining("/api/regeneration/close-confirm"),
    ]);
    expect((captured[0].body as RegenerationQuery).phase_state).toBe("open");
    expect((captured[1].body as CloseSummaryRequest).phase_state).toBe("close");
  });

  it("getChatTransport returns a singleton and setChatTransport swaps it for tests", async () => {
    const a = getChatTransport();
    const b = getChatTransport();
    expect(a).toBe(b);

    const fake: ChatTransport = {
      sendQuery: vi.fn(async () => ({}) as RegenerationResponse),
      sendCloseSummary: vi.fn(async () => ({} as never)),
      sendCloseConfirm: vi.fn(async () => ({} as never)),
    };
    setChatTransport(fake);
    expect(getChatTransport()).toBe(fake);
  });
});
