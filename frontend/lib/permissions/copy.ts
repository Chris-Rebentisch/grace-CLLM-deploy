/**
 * EC-12 forbidden-vocabulary-clean copy registry for Permission Matrix UI.
 *
 * Chunk 42 prompt §12 (Risk R13): user-facing copy MUST NOT use any of
 * the eight forbidden tokens enumerated in D281+D289. This module is the
 * single source of truth for any string that surfaces in
 * `frontend/app/permissions/**` or `frontend/components/permissions/**`.
 *
 * To add new copy, append a new key here and assert `assertEC12Clean(value)`.
 * The accompanying `permissions-copy-EC12.test.ts` is the CI fence — do not
 * inline raw strings in the components.
 */

/**
 * Forbidden vocabulary set (D281+D289). Keep this list authoritative —
 * the EC-12 lint test imports it directly.
 *
 * Tokens chosen for risks tracked in the docs/elicitation-catalog-governance.md
 * registry. Substring match is case-insensitive.
 */
export const EC12_FORBIDDEN_TOKENS: ReadonlyArray<string> = [
  "blame",
  "punish",
  "punishment",
  "penalty",
  "penalize",
  "cooldown",
  "decay",
  "shame",
];

export function assertEC12Clean(value: string): string {
  const lower = value.toLowerCase();
  for (const token of EC12_FORBIDDEN_TOKENS) {
    if (lower.includes(token.toLowerCase())) {
      throw new Error(
        `EC-12 violation: forbidden token "${token}" found in copy: ${value}`,
      );
    }
  }
  return value;
}

/**
 * Chunk 42 user-facing copy. Bands are surfaced as labels only
 * (D120/D217 — no numeric distance scores).
 */
export const PERMISSIONS_COPY = {
  pageTitle: assertEC12Clean("Permission matrices"),
  activeMatrixHeading: assertEC12Clean("Active matrix"),
  noActiveMatrix: assertEC12Clean(
    "No matrix has been ratified yet. Trigger a hypothesis generation run, then ratify the proposed matrix to activate access controls.",
  ),
  ratifyHeading: assertEC12Clean("Ratify proposed matrix"),
  ratifyConfirm: assertEC12Clean("Confirm ratification"),
  ratifyCancel: assertEC12Clean("Cancel"),
  ratifyDescription: assertEC12Clean(
    "Once ratified, this matrix becomes the active access policy and is appended to the hash-chained governance log. Append-only — subsequent edits require a new ratification.",
  ),
  driftQueueHeading: assertEC12Clean("Drift detection queue"),
  driftQueueRunbookHint: assertEC12Clean(
    "Pending items have no in-app decide action in v1 — use your operator runbook.",
  ),
  driftBandHigh: assertEC12Clean("Strong match"),
  driftBandMedium: assertEC12Clean("Partial match"),
  driftBandLow: assertEC12Clean("No strong match"),
  driftRationaleHigh: assertEC12Clean(
    "Strong cluster centroid match; auto-assigned.",
  ),
  driftRationaleMedium: assertEC12Clean(
    "Partial cluster match; pre-filled guess for review.",
  ),
  driftRationaleLow: assertEC12Clean(
    "No strong cluster match; queued for manual review.",
  ),
  hypothesisConfidenceStrong: assertEC12Clean("Strong evidence"),
  hypothesisConfidenceModerate: assertEC12Clean("Moderate evidence"),
  hypothesisConfidenceWeak: assertEC12Clean("Weak evidence"),
  evidenceBundleHeading: assertEC12Clean("Evidence overlay"),
  evidenceBundleEmpty: assertEC12Clean(
    "No evidence sections collected for this cluster yet.",
  ),
  decisionAccept: assertEC12Clean("Accept cluster"),
  decisionReject: assertEC12Clean("Reject cluster"),
  decisionReassign: assertEC12Clean("Reassign members"),
  decisionRename: assertEC12Clean("Rename cluster"),
} as const;

export type PermissionsCopyKey = keyof typeof PERMISSIONS_COPY;
