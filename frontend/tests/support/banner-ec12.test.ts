import { describe, expect, it } from "vitest";

import { BANNER_COPY } from "@/lib/support/banner_copy";
import { EC12_FORBIDDEN_TOKENS } from "@/lib/permissions/copy";

describe("support banner copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(BANNER_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token.toLowerCase()),
        `forbidden token "${token}" present in banner copy`,
      ).toBe(false);
    }
  });

  it("all copy strings are non-empty", () => {
    for (const [key, value] of Object.entries(BANNER_COPY)) {
      expect(value.length, `${key} should be non-empty`).toBeGreaterThan(0);
    }
  });
});
