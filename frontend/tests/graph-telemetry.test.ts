import { describe, expect, it } from "vitest";
import { EventFactory } from "@/lib/telemetry/events";
import { sha256Hex } from "@/lib/ids/hash";

const SID = "00000000-0000-4000-8000-000000000001";

describe("Chunk 28 telemetry factories (D215)", () => {
  it("buildGraphViewerOpened + buildGraphNodeInspected produce valid envelope shapes", () => {
    const opened = EventFactory.graphViewerOpened(SID, "open", {
      scope: "all",
      entity_count_estimated: 42,
    });
    expect(opened.event_type).toBe("graph_viewer_opened");
    expect(opened.session_id).toBe(SID);
    expect(opened.phase_name).toBe("open");
    expect(opened.payload).toEqual({
      scope: "all",
      entity_count_estimated: 42,
    });
    expect(opened.schema_version).toBe(1);
    expect(opened.payload_schema_version).toBe(1);

    const node = EventFactory.graphNodeInspected(SID, "open", {
      entity_type: "Legal_Entity",
      grace_id_hash: "a".repeat(64),
    });
    expect(node.event_type).toBe("graph_node_inspected");
    expect(node.payload.grace_id_hash).toBe("a".repeat(64));
  });

  it("sha256Hex produces 64-char hex and is deterministic", async () => {
    const a = await sha256Hex("grace-id-seed-1");
    const b = await sha256Hex("grace-id-seed-1");
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
    const c = await sha256Hex("grace-id-seed-2");
    expect(a).not.toBe(c);
  });
});
