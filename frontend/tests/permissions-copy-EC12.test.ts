import { describe, expect, it } from "vitest";

import {
  EC12_FORBIDDEN_TOKENS,
  PERMISSIONS_COPY,
  assertEC12Clean,
} from "@/lib/permissions/copy";

describe("permissions copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(PERMISSIONS_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token.toLowerCase()),
        `forbidden token "${token}" present in permissions copy`,
      ).toBe(false);
    }
  });

  it("assertEC12Clean throws on every forbidden token", () => {
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(() => assertEC12Clean(`Sample ${token} message`)).toThrow(
        /EC-12 violation/,
      );
    }
    // Sanity: clean strings pass.
    expect(assertEC12Clean("All clear")).toBe("All clear");
  });
});
