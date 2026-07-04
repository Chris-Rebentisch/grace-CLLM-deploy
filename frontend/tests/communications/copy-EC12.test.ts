import { describe, it, expect } from "vitest";
import {
  COMMUNICATIONS_COPY,
  EC12_FORBIDDEN_TOKENS,
  assertEC12Clean,
} from "@/lib/communications/copy";

describe("communications copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(COMMUNICATIONS_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(corpus).not.toContain(token);
    }
  });

  it("assertEC12Clean throws on every forbidden token", () => {
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(() => assertEC12Clean(`some ${token} text`)).toThrow(
        /EC-12 violation/
      );
    }
  });

  it("exposes the expected copy keys", () => {
    expect(COMMUNICATIONS_COPY.pageTitle).toBeDefined();
    expect(COMMUNICATIONS_COPY.dpiaActiveLabel).toBeDefined();
    expect(COMMUNICATIONS_COPY.dpiaInactiveLabel).toBeDefined();
    expect(COMMUNICATIONS_COPY.submitButton).toBeDefined();
  });

  it("exposes profile browser copy entries (Chunk 60)", () => {
    expect(COMMUNICATIONS_COPY.profileListTitle).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileListSearchPlaceholder).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileListEmpty).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileDetailBackLink).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileDetailStyleSignatureHeading).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileDetailRecipientsHeading).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileDetailProvisionalAdvisory).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileAggregateBackLink).toBeDefined();
    expect(COMMUNICATIONS_COPY.profileAggregatePrefix).toBeDefined();
  });

  it("Chunk 60 communications copy passes extended forbidden vocabulary", () => {
    const extendedForbidden = [
      "incorrect", "failure", "deficit", "drift", "blind spot",
      "mistake", "wrong", "bad", "unprofessional", "inappropriate",
    ];
    const corpus = Object.values(COMMUNICATIONS_COPY).join(" ").toLowerCase();
    for (const token of extendedForbidden) {
      expect(corpus).not.toContain(token.toLowerCase());
    }
  });
});
