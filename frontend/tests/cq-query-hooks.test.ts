import { describe, expect, it } from "vitest";

describe("CQ query hooks", () => {
  it("useCQCandidates polling hook exists", async () => {
    const mod = await import("@/lib/query/cq");
    expect(typeof mod.useCQCandidates).toBe("function");
  });

  it("useCQCreate hook shape", async () => {
    const mod = await import("@/lib/query/cq");
    expect(typeof mod.useCQCreate).toBe("function");
  });
});
