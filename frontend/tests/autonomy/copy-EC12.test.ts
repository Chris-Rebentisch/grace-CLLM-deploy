import { describe, expect, it } from "vitest";

import {
  EC12_FORBIDDEN_TOKENS,
  AUTONOMY_COPY,
  assertEC12Clean,
} from "@/lib/autonomy/copy";

describe("autonomy copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(AUTONOMY_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token.toLowerCase()),
        `forbidden token "${token}" present in autonomy copy`,
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

  it("exposes the three trust-indicator labels", () => {
    expect(AUTONOMY_COPY.trustIndicatorHigh).toBeTruthy();
    expect(AUTONOMY_COPY.trustIndicatorBuilding).toBeTruthy();
    expect(AUTONOMY_COPY.trustIndicatorInsufficient).toBeTruthy();
  });

  it("exposes the three band-approval labels", () => {
    expect(AUTONOMY_COPY.bandApprovalHigh).toBeTruthy();
    expect(AUTONOMY_COPY.bandApprovalMedium).toBeTruthy();
    expect(AUTONOMY_COPY.bandApprovalLow).toBeTruthy();
  });

  // Chunk 65 D446–D448 Governance Audit-Trail Hardening.
  it("exposes reason textarea copy strings", () => {
    expect(AUTONOMY_COPY.killSwitchReasonPlaceholder).toBeTruthy();
    expect(AUTONOMY_COPY.killSwitchReasonLabel).toBeTruthy();
  });

  it("exposes restore-state dialog copy strings", () => {
    expect(AUTONOMY_COPY.restoreStateHeading).toBeTruthy();
    expect(AUTONOMY_COPY.restoreStateBody).toBeTruthy();
    expect(AUTONOMY_COPY.restoreStateTierEnabled).toBeTruthy();
    expect(AUTONOMY_COPY.restoreStateTierDisabled).toBeTruthy();
    expect(AUTONOMY_COPY.restoreStateConfirm).toBeTruthy();
    expect(AUTONOMY_COPY.restoreStateCancel).toBeTruthy();
  });

  it("exposes force-disengage label", () => {
    expect(AUTONOMY_COPY.forceDisengageLabel).toBeTruthy();
  });
});
