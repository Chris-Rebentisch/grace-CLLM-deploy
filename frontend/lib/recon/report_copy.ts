// Reconciliation Layer copy registry (Chunk 37, D288).
//
// Every user-facing string in `frontend/components/recon/*` MUST come
// from this module. The strings are scanned for forbidden tokens by:
//
//   * `tests/elicitation/test_ec_constraints.py::test_ec_11`
//     (filesystem read of this file at the canonical path).
//   * `frontend/lib/recon/__tests__/report-copy.test.ts`
//     (in-process import; mirror of the canonical Python list).
//
// The canonical token list lives in
// `tests/elicitation/test_ec_constraints.py:_RECON_FORBIDDEN_TOKENS`.
// Update that list first; the in-process mirror below tracks it in
// lock-step.
//
// EVERY string in this file is opportunity-framed (EC-11). The canonical
// alias for what older drafts called "ERD" is `evidence_grounding`.

export const DIVERGENCE_MAP_TITLE =
  "Cross-Executive Divergence Map";

export const DIVERGENCE_MAP_SUBTITLE =
  "What two reviewers chose to emphasize, side by side.";

export const DIVERGENCE_MAP_BUCKET_LABELS = {
  additive_A: "Reviewer A emphasized",
  additive_B: "Reviewer B emphasized",
  contradictory: "Choices that point in different directions",
  consensus: "Choices both reviewers made",
} as const;

export const DIVERGENCE_MAP_EVIDENCE_BADGE = (count: number): string =>
  `Evidence: ${count}`;

export const DIVERGENCE_MAP_TABS_FALLBACK_HINT =
  "On narrow screens, use the tabs above to switch reviewers.";

export const DIVERGENCE_MAP_DRAWER_TITLE =
  "Evidence behind this choice";

export const DIVERGENCE_MAP_EMPTY_STATE =
  "Both reviewers made the same choices for this segment.";

export const DOCUMENTED_REALITY_TITLE =
  "Documented Reality Report";

export const DOCUMENTED_REALITY_SUBTITLE =
  "What the evidence in your knowledge graph documents.";

export const DOCUMENTED_REALITY_AGGREGATIONS_TOGGLE =
  "Show aggregation data";

export const DOCUMENTED_REALITY_AGGREGATIONS_HIDE =
  "Hide aggregation data";

export const DOCUMENTED_REALITY_BELOW_FLOOR_NOTICE =
  "The corpus is still small; growing the evidence base is an opportunity to enrich the next report.";

export const DOCUMENTED_REALITY_NARRATIVE_PLACEHOLDER =
  "Narrative will appear here once the report is generated.";

export const SCHEDULE_EDITOR_TITLE =
  "Schedule Documented Reality Reports";

export const SCHEDULE_EDITOR_CADENCE_LABELS = {
  quarterly: "Quarterly",
  monthly: "Monthly",
  on_demand: "On demand only",
} as const;

export const SCHEDULE_EDITOR_PREVIEW = (cadence: string): string => {
  if (cadence === "monthly") return "Reports will run on the first of each month.";
  if (cadence === "quarterly")
    return "Reports will run on the first day of each quarter.";
  return "Reports will run only when manually triggered.";
};

export const SCHEDULE_EDITOR_SUBMIT = "Save schedule";
export const SCHEDULE_EDITOR_CANCEL = "Cancel";

export const GAP_REPORT_TITLE = "Gap Report";

export const GAP_REPORT_SUBTITLE =
  "Where evidence and emphasis are aligned, and where they could grow together.";

export const GAP_REPORT_SECTION_LABELS = {
  emphasized_with_evidence: "Emphasized choices supported by evidence",
  emphasized_without_evidence: "Emphasized choices that could use stronger evidence",
  unemphasized_in_evidence: "Evidence-supported elements not yet emphasized",
} as const;

export const GAP_REPORT_EMPTY =
  "No Gap Report has been generated for this session yet.";

// D297 — Reconciliation Bridge "change-in-flight" framing (Chunk 38).
export const COVERING_DIRECTIVES_HEADING =
  "Covered by an active Change Directive";

export const COVERING_DIRECTIVES_SUBTITLE =
  "This segment is part of a recorded organizational change in flight; the items below frame current evidence as part of that intentional change.";

export const COVERING_DIRECTIVES_EMPTY = "";

export const RECONCILIATION_SIDEBAR_TITLE = "Reconciliation";
export const RECONCILIATION_SIDEBAR_DIVERGENCE_MAP = "Divergence Map";
export const RECONCILIATION_SIDEBAR_DOCUMENTED_REALITY = "Documented Reality";
export const RECONCILIATION_SIDEBAR_GAP_REPORT = "Gap Report";

export const RECON_FORBIDDEN_TOKENS_MIRROR: ReadonlyArray<string> = [
  // Mirror of tests/elicitation/test_ec_constraints.py:_RECON_FORBIDDEN_TOKENS.
  // Update the Python list first; this list is the test-side mirror.
  "drift",
  "blind spot",
  "mistake",
  "wrong",
  "reality gap",
  "incorrect",
  "failure",
  "deficit",
];
