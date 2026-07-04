import { describe, expect, it } from "vitest";

import { COPY, FORBIDDEN_TOKENS } from "@/lib/change-directives/copy";

function flatten(obj: unknown, acc: string[] = []): string[] {
  if (typeof obj === "string") {
    acc.push(obj);
    return acc;
  }
  if (Array.isArray(obj)) {
    for (const item of obj) flatten(item, acc);
    return acc;
  }
  if (obj && typeof obj === "object") {
    for (const value of Object.values(obj)) flatten(value, acc);
  }
  return acc;
}

describe("change-directives copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = flatten(COPY).join(" ").toLowerCase();
    for (const token of FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token),
        `forbidden token "${token}" present in change-directive copy`,
      ).toBe(false);
    }
  });
});
