import { describe, expect, it } from "vitest";

import {
  EC12_FORBIDDEN_TOKENS,
  PROPOSALS_COPY,
  assertEC12Clean,
} from "@/lib/proposals/copy";

describe("proposals copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(PROPOSALS_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token.toLowerCase()),
        `forbidden token "${token}" present in proposals copy`,
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

  it("exposes the three confidence-band labels", () => {
    expect(PROPOSALS_COPY.confidenceBandHigh).toBeTruthy();
    expect(PROPOSALS_COPY.confidenceBandMedium).toBeTruthy();
    expect(PROPOSALS_COPY.confidenceBandLow).toBeTruthy();
  });

  it("exposes the three priority labels", () => {
    expect(PROPOSALS_COPY.priorityHigh).toBeTruthy();
    expect(PROPOSALS_COPY.priorityMedium).toBeTruthy();
    expect(PROPOSALS_COPY.priorityLow).toBeTruthy();
  });

  it("exposes decision defer label", () => {
    expect(PROPOSALS_COPY.decisionDefer).toBeTruthy();
  });

  it("exposes deferred and superseded status labels", () => {
    expect(PROPOSALS_COPY.statusDeferred).toBeTruthy();
    expect(PROPOSALS_COPY.statusSuperseded).toBeTruthy();
  });
});
