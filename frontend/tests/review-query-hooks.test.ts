import { describe, expect, it } from "vitest";

describe("review query hooks", () => {
  it("useReviewSession hook shape", async () => {
    const mod = await import("@/lib/query/review");
    expect(typeof mod.useReviewSession).toBe("function");
    expect(typeof mod.useDecide).toBe("function");
  });

  it("useCQImpactPreview hook shape", async () => {
    const mod = await import("@/lib/query/review");
    expect(typeof mod.useCQImpactPreview).toBe("function");
  });
});
