/**
 * EC-12 forbidden-vocabulary-clean copy registry for the Proposals
 * surface (Chunk 47, CP7 / D389).
 *
 * Mirrors `frontend/lib/permissions/copy.ts` (Chunk 42 D336): every
 * user-facing string for `frontend/app/proposals/**` and
 * `frontend/components/proposals/**` must come through this module.
 * `assertEC12Clean` runs at module load — a forbidden-token leak fails
 * the import.
 *
 * D120/D217: raw_confidence never rendered as numeric — band labels only.
 */
import {
  EC12_FORBIDDEN_TOKENS,
  assertEC12Clean,
} from "@/lib/permissions/copy";

export { EC12_FORBIDDEN_TOKENS, assertEC12Clean };

export const PROPOSALS_COPY = {
  pageTitle: assertEC12Clean("Schema proposals"),
  emptyState: assertEC12Clean(
    "No schema proposals have been generated yet. Run the signal-to-proposal pipeline to create proposals.",
  ),
  filterTier: assertEC12Clean("Tier"),
  filterStatus: assertEC12Clean("Status"),
  filterModule: assertEC12Clean("Module"),
  filterAny: assertEC12Clean("any"),
  statusPending: assertEC12Clean("Pending"),
  statusApproved: assertEC12Clean("Approved"),
  statusRejected: assertEC12Clean("Rejected"),
  statusModified: assertEC12Clean("Modified"),
  statusDeferred: assertEC12Clean("Deferred"),
  statusSuperseded: assertEC12Clean("Superseded"),
  statusAutoApplied: assertEC12Clean("Auto-applied"),
  priorityHigh: assertEC12Clean("High priority"),
  priorityMedium: assertEC12Clean("Medium priority"),
  priorityLow: assertEC12Clean("Low priority"),
  evidenceHeading: assertEC12Clean("Evidence bundle"),
  evidenceSignalType: assertEC12Clean("Signal type"),
  evidenceAffectedTypes: assertEC12Clean("Affected entity types"),
  evidenceModule: assertEC12Clean("Module"),
  evidenceSummary: assertEC12Clean("Evidence summary"),
  evidenceNoSummary: assertEC12Clean("No summary available."),
  evidenceExampleSnippets: assertEC12Clean("Example snippets"),
  evidenceExampleDocuments: assertEC12Clean("Example documents"),
  decisionHeading: assertEC12Clean("Review decision"),
  decisionApprove: assertEC12Clean("Approve"),
  decisionReject: assertEC12Clean("Reject"),
  decisionModify: assertEC12Clean("Approve with modifications"),
  decisionDefer: assertEC12Clean("Defer"),
  decisionReviewer: assertEC12Clean("Reviewer"),
  decisionReviewerPlaceholder: assertEC12Clean("Enter reviewer identifier"),
  kgclHeading: assertEC12Clean("Proposed change"),
  overflowBanner: assertEC12Clean(
    "Queue depth has exceeded the soft cap — this proposal is overflow.",
  ),
  detailNotFound: assertEC12Clean("Proposal not found."),
  confidenceBandHigh: assertEC12Clean("Strong signal"),
  confidenceBandMedium: assertEC12Clean("Moderate signal"),
  confidenceBandLow: assertEC12Clean("Weak signal"),
} as const;

export type ProposalsCopyKey = keyof typeof PROPOSALS_COPY;
