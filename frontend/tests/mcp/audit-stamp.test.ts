import { describe, expect, it } from "vitest";
import type { ElicitationEventEnvelope } from "@/lib/api/types";

describe("MCP agent identity audit-stamp (D364, CP7)", () => {
  it("agent_id is an optional field on ElicitationEventEnvelope", () => {
    // Envelope without agent identity fields should compile and work.
    const envelope: ElicitationEventEnvelope = {
      event_id: "e1",
      event_type: "session_started",
      session_id: "s1",
      actor_type: "human",
      phase_name: "open",
      emitted_at: new Date().toISOString(),
      schema_version: 1,
      grace_version: "0.44.0",
      payload: {},
      payload_schema_version: 1,
    };
    expect(envelope.agent_id).toBeUndefined();
    expect(envelope.delegation_source).toBeUndefined();
  });

  it("agent identity fields accept valid values", () => {
    const envelope: ElicitationEventEnvelope = {
      event_id: "e2",
      event_type: "mcp_review_decided",
      session_id: "s2",
      actor_type: "agent",
      phase_name: "structure",
      emitted_at: new Date().toISOString(),
      schema_version: 1,
      grace_version: "0.44.0",
      payload: {},
      payload_schema_version: 1,
      agent_id: "cowork-1",
      agent_display_name: "Cowork Plugin",
      delegation_source: "agent_on_behalf",
    };
    expect(envelope.agent_id).toBe("cowork-1");
    expect(envelope.agent_display_name).toBe("Cowork Plugin");
    expect(envelope.delegation_source).toBe("agent_on_behalf");
  });

  it("delegation_source accepts all three valid values", () => {
    const valid: Array<"user_direct" | "agent_on_behalf" | "system_scheduled"> = [
      "user_direct",
      "agent_on_behalf",
      "system_scheduled",
    ];
    for (const ds of valid) {
      const envelope: ElicitationEventEnvelope = {
        event_id: `e-${ds}`,
        event_type: "session_started",
        session_id: "s3",
        actor_type: "human",
        phase_name: "none",
        emitted_at: new Date().toISOString(),
        schema_version: 1,
        grace_version: "0.44.0",
        payload: {},
        payload_schema_version: 1,
        delegation_source: ds,
      };
      expect(envelope.delegation_source).toBe(ds);
    }
  });

  it("null values accepted for agent identity fields", () => {
    const envelope: ElicitationEventEnvelope = {
      event_id: "e4",
      event_type: "session_started",
      session_id: "s4",
      actor_type: "human",
      phase_name: "none",
      emitted_at: new Date().toISOString(),
      schema_version: 1,
      grace_version: "0.44.0",
      payload: {},
      payload_schema_version: 1,
      agent_id: null,
      agent_display_name: null,
      delegation_source: null,
    };
    expect(envelope.agent_id).toBeNull();
    expect(envelope.delegation_source).toBeNull();
  });
});
