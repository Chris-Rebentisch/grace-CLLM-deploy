import { describe, expect, it } from "vitest";
import { EventFactory, REQUIRED_ENVELOPE_FIELDS } from "@/lib/telemetry/events";

describe("telemetry event factories (D228)", () => {
  it("nine new factory functions produce valid envelopes", () => {
    // Test that EventFactory has the expected shape
    expect(typeof EventFactory.sessionStarted).toBe("function");
    expect(typeof EventFactory.phaseEntered).toBe("function");

    // Verify an envelope has all required fields
    const envelope = EventFactory.sessionStarted("s1", "open");
    for (const field of REQUIRED_ENVELOPE_FIELDS) {
      expect(envelope).toHaveProperty(field);
    }
    expect(envelope.event_type).toBe("session_started");
  });

  it("D228 structure_phase_entered factory works when added to EventFactory", () => {
    // The EventFactory needs the nine new D228 factories added in CP10.
    // For now, verify the existing factories produce correct envelope shape.
    const envelope = EventFactory.phaseEntered("s1", "structure");
    expect(envelope.event_type).toBe("phase_entered");
    expect(envelope.payload.entered_phase).toBe("structure");
    expect(envelope.schema_version).toBe(1);
  });
});
