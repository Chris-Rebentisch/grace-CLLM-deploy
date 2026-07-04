// Tests for `frontend/lib/recon/report_copy.ts` (Chunk 37, D289 / EC-11).
// Mirror of the canonical Python list at
// tests/elicitation/test_ec_constraints.py:_RECON_FORBIDDEN_TOKENS.

import { describe, expect, it } from "vitest";

import * as ReportCopy from "@/lib/recon/report_copy";

const RECON_FORBIDDEN_TOKENS = [
  "drift",
  "blind spot",
  "mistake",
  "wrong",
  "reality gap",
  "incorrect",
  "failure",
  "deficit",
];

describe("frontend/lib/recon/report_copy.ts (Chunk 37, D289 / EC-11)", () => {
  it("exports at least one user-facing string", () => {
    const stringExports: string[] = [];
    for (const value of Object.values(ReportCopy)) {
      if (typeof value === "string") stringExports.push(value);
      else if (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value)
      ) {
        for (const v of Object.values(value as Record<string, unknown>)) {
          if (typeof v === "string") stringExports.push(v);
        }
      }
    }
    expect(stringExports.length).toBeGreaterThan(0);
  });

  it("contains no forbidden tokens", () => {
    const violations: { exportName: string; token: string }[] = [];
    for (const [exportName, value] of Object.entries(ReportCopy)) {
      // Skip the mirror list itself — it intentionally contains the tokens.
      if (exportName === "RECON_FORBIDDEN_TOKENS_MIRROR") continue;
      const collected: string[] = [];
      if (typeof value === "string") collected.push(value);
      else if (
        value !== null &&
        typeof value === "object" &&
        !Array.isArray(value)
      ) {
        for (const v of Object.values(value as Record<string, unknown>)) {
          if (typeof v === "string") collected.push(v);
        }
      }
      const body = collected.join(" ").toLowerCase();
      for (const token of RECON_FORBIDDEN_TOKENS) {
        if (body.includes(token)) {
          violations.push({ exportName, token });
        }
      }
    }
    expect(violations).toEqual([]);
  });
});
