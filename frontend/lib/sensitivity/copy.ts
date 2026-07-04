/**
 * EC-12 forbidden-vocabulary-clean copy registry for the Sensitivity
 * Gate Compliance Surface (Chunk 43, CP6 / D344).
 *
 * Mirrors `frontend/lib/permissions/copy.ts` (Chunk 42 D336): every
 * user-facing string for `frontend/app/sensitivity/**` and
 * `frontend/components/sensitivity/**` must come through this module.
 * `assertEC12Clean` runs at module load — a forbidden-token leak fails
 * the import.
 *
 * D120/D217: bands surface as label strings only — never raw
 * `coverage_score` floats. The backend strips `coverage_score` before
 * serialization (`src/api/sensitivity_routes.py:_strip_coverage_score`).
 */
import {
  EC12_FORBIDDEN_TOKENS,
  assertEC12Clean,
} from "@/lib/permissions/copy";

export { EC12_FORBIDDEN_TOKENS, assertEC12Clean };

export const SENSITIVITY_COPY = {
  pageTitle: assertEC12Clean("Sensitivity gate"),
  taggedSubsetHeading: assertEC12Clean("Tagged cluster decisions"),
  taggedSubsetEmpty: assertEC12Clean(
    "No cluster decisions on the active matrix carry a sensitivity tag yet.",
  ),
  reportLatestHeading: assertEC12Clean("Latest classification report"),
  reportNone: assertEC12Clean(
    "No sensitivity classification report has been generated for the active matrix yet.",
  ),
  reportGenerateCta: assertEC12Clean("Generate sensitivity report"),
  reportRegenerateCta: assertEC12Clean("Regenerate report"),
  reportRatifyDescription: assertEC12Clean(
    "Generates a render-only classification report over the active matrix. Reports are append-only — successive runs persist as new rows. Force regeneration is rate-limited to one per minute per matrix.",
  ),
  reportRatifyConfirm: assertEC12Clean("Generate report"),
  reportRatifyCancel: assertEC12Clean("Cancel"),
  coverageBandHigh: assertEC12Clean("High coverage"),
  coverageBandMedium: assertEC12Clean("Partial coverage"),
  coverageBandLow: assertEC12Clean("Low coverage"),
  coverageBandUnknown: assertEC12Clean("Below tag floor"),
  tagInventoryHeading: assertEC12Clean("Tag inventory"),
  tagInventoryEmpty: assertEC12Clean("No tags found on the active matrix."),
  coverageBreakdownHeading: assertEC12Clean(
    "Coverage by resource and action",
  ),
  coverageBreakdownEmpty: assertEC12Clean(
    "No access rules to summarize for the active matrix.",
  ),
  untaggedRulesHeading: assertEC12Clean("Untagged access rules"),
  untaggedRulesEmpty: assertEC12Clean(
    "Every access rule on the active matrix carries at least one tag.",
  ),
  untaggedRulesTruncated: assertEC12Clean(
    "Untagged rule list capped — only the first 1000 rules are shown.",
  ),
  hygieneFindingsHeading: assertEC12Clean("Tag hygiene findings"),
  hygieneFindingsEmpty: assertEC12Clean("No near-duplicate tag names detected."),
  belowFloorBanner: assertEC12Clean(
    "The active matrix carries no sensitivity tags yet. Coverage classification is unavailable until tags are added on at least one access rule.",
  ),
  auditTrailHeading: assertEC12Clean("Sensitivity audit trail"),
  auditTrailEmpty: assertEC12Clean(
    "No retrieval query events match this tag.",
  ),
  auditTrailFilterPrompt: assertEC12Clean(
    "Filter retrieval query events by sensitivity tag.",
  ),
  auditTrailTagInputLabel: assertEC12Clean("Tag"),
  auditTrailTagInputPlaceholder: assertEC12Clean("e.g. pii"),
  auditTrailApply: assertEC12Clean("Apply filter"),
  auditTrailRunbookHint: assertEC12Clean(
    "Audit-trail body wires up once the ArcadeDB Query_Event tag property ships in CP5.",
  ),
} as const;

export type SensitivityCopyKey = keyof typeof SENSITIVITY_COPY;
