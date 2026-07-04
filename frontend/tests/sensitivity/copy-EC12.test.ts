import { describe, expect, it } from "vitest";

import {
  EC12_FORBIDDEN_TOKENS,
  SENSITIVITY_COPY,
  assertEC12Clean,
} from "@/lib/sensitivity/copy";

describe("sensitivity copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(SENSITIVITY_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token.toLowerCase()),
        `forbidden token "${token}" present in sensitivity copy`,
      ).toBe(false);
    }
  });

  it("assertEC12Clean throws on every forbidden token", () => {
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(() => assertEC12Clean(`Sample ${token} message`)).toThrow(
        /EC-12 violation/,
      );
    }
    expect(assertEC12Clean("All clear")).toBe("All clear");
  });

  it("exposes the three coverage-band labels", () => {
    expect(SENSITIVITY_COPY.coverageBandHigh).toBeTruthy();
    expect(SENSITIVITY_COPY.coverageBandMedium).toBeTruthy();
    expect(SENSITIVITY_COPY.coverageBandLow).toBeTruthy();
    expect(SENSITIVITY_COPY.coverageBandUnknown).toBeTruthy();
  });
});
