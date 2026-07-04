import { describe, expect, it } from "vitest";

describe("useScopeSegments hook", () => {
  it("hook shape exports correctly", async () => {
    const mod = await import("@/lib/query/scope");
    expect(typeof mod.useScopeSegments).toBe("function");
  });
});
