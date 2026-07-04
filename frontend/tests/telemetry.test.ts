import { afterEach, describe, expect, it, vi } from "vitest";
import {
  EventFactory,
  REQUIRED_ENVELOPE_FIELDS,
  buildEnvelope,
} from "@/lib/telemetry/events";
import { postElicitationEvent } from "@/lib/telemetry/emit";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("telemetry event factories + emitter", () => {
  it("produces envelopes with every protocol §8.2 required field + correct typed payloads", () => {
    const envelope = EventFactory.sessionResumed(
      "00000000-0000-0000-0000-000000000001",
      "open",
      1500,
    );
    for (const field of REQUIRED_ENVELOPE_FIELDS) {
      expect(
        envelope[field as keyof typeof envelope],
        `missing required field: ${String(field)}`,
      ).not.toBeUndefined();
    }
    expect(envelope.event_type).toBe("session_resumed");
    const payload = envelope.payload as Record<string, unknown>;
    expect(payload.resumed_to_phase).toBe("open");
    expect(payload.paused_duration_ms).toBe(1500);
    // EC-5 audit on the client-side factory.
    expect(payload.cooldown).toBeUndefined();
    expect(payload.penalty).toBeUndefined();
    expect(payload.decay).toBeUndefined();

    // buildEnvelope is the primitive; ensure explicit actor_type override works.
    const custom = buildEnvelope({
      session_id: envelope.session_id,
      phase_name: "open",
      event_type: "phase_entered",
      payload: { entered_phase: "open", entered_at: new Date().toISOString() },
      actor_type: "system",
    });
    expect(custom.actor_type).toBe("system");
  });

  it("postElicitationEvent posts to /api/elicitation/events and logs on 422 without throwing", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    globalThis.fetch = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return new Response(JSON.stringify({ event_id: "x", accepted_at: "now" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      });
    }) as unknown as typeof fetch;

    const env = EventFactory.sessionStarted(
      "00000000-0000-0000-0000-000000000001",
      "open",
    );
    const ack = await postElicitationEvent(env);
    expect(ack?.event_id).toBe("x");
    expect(calls[0].url).toMatch(/\/api\/elicitation\/events$/);
    const headers = (calls[0].init?.headers ?? {}) as Record<string, string>;
    expect(headers["X-Graph-Scope"]).toBe("all");

    // 422 → returns null, does not throw.
    globalThis.fetch = (async () =>
      new Response(
        JSON.stringify({
          detail: {
            error_type: "telemetry_validation_error",
            errors: [{ loc: ["payload", "entered_phase"], msg: "required" }],
          },
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      )) as unknown as typeof fetch;

    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const result = await postElicitationEvent(env);
    expect(result).toBeNull();
    expect(warn).toHaveBeenCalled();
  });
});
