/**
 * EC-12 forbidden-vocabulary-clean copy registry for Earned Autonomy
 * Calibration surface (Chunk 49, CP8 / D394–D397).
 *
 * Mirrors `frontend/lib/permissions/copy.ts` (Chunk 42 D336): every
 * user-facing string for `frontend/app/autonomy/**` and
 * `frontend/components/autonomy/**` must come through this module.
 * `assertEC12Clean` runs at module load — a forbidden-token leak fails
 * the import.
 *
 * D120/D217: trust indicators surface as label strings only — never raw
 * `trust_score` floats, `approval_rate` numerics, or `sample_count`
 * values. The backend dashboard route strips no fields (the UI is
 * responsible for rendering bands and labels only).
 */
import {
  EC12_FORBIDDEN_TOKENS,
  assertEC12Clean,
} from "@/lib/permissions/copy";

export { EC12_FORBIDDEN_TOKENS, assertEC12Clean };

export const AUTONOMY_COPY = {
  pageTitle: assertEC12Clean("Earned autonomy calibration"),
  pageDescription: assertEC12Clean(
    "Per-tier reliability calibration and autonomy readiness for schema change proposals.",
  ),
  tierHeading: assertEC12Clean("Change tier"),
  tierLabel1: assertEC12Clean("Tier 1 — Low risk"),
  tierLabel2: assertEC12Clean("Tier 2 — Medium risk"),
  tierLabel3: assertEC12Clean("Tier 3 — High risk"),
  trustIndicatorHigh: assertEC12Clean("Ready for autonomy"),
  trustIndicatorBuilding: assertEC12Clean("Building track record"),
  trustIndicatorInsufficient: assertEC12Clean("Insufficient reviews"),
  reliabilityHeading: assertEC12Clean("Reliability by confidence band"),
  reliabilityEmpty: assertEC12Clean(
    "No calibration bands computed yet. Run the calibration updater after recording decisions.",
  ),
  progressHeading: assertEC12Clean("Calibration progress"),
  progressGateLabel: assertEC12Clean("Reviews toward gate"),
  riskToleranceHeading: assertEC12Clean("Risk tolerance configuration"),
  riskToleranceLabel: assertEC12Clean("Acceptable approval rate"),
  windowSizeLabel: assertEC12Clean("Rolling window size"),
  minReviewsLabel: assertEC12Clean("Minimum reviews for calibration"),
  configSaved: assertEC12Clean("Configuration updated"),
  configError: assertEC12Clean("Failed to update configuration"),
  regressionBanner: assertEC12Clean(
    "Regression detected — recent approval rate has dropped below the historical lower bound.",
  ),
  dashboardError: assertEC12Clean("Failed to load calibration dashboard"),
  noData: assertEC12Clean("No calibration data available yet."),
  bandApprovalHigh: assertEC12Clean("Consistently approved"),
  bandApprovalMedium: assertEC12Clean("Mixed outcomes"),
  bandApprovalLow: assertEC12Clean("Frequently revised"),
  // Chunk 50 D398–D401 Agent Daemon kill switch + cooling UX.
  killSwitchHeading: assertEC12Clean("Autonomous evolution"),
  killSwitchEngage: assertEC12Clean("Stop autonomous evolution"),
  killSwitchDisengage: assertEC12Clean("Resume autonomous evolution"),
  killSwitchDisengageConfirm: assertEC12Clean(
    "Resume autonomous schema evolution? The daemon will begin evaluating and applying low-risk proposals again.",
  ),
  killSwitchDisengageCancel: assertEC12Clean("Cancel"),
  killSwitchDisengageConfirmButton: assertEC12Clean("Resume"),
  killSwitchStatusActive: assertEC12Clean("Autonomy active"),
  killSwitchStatusStopped: assertEC12Clean("Autonomy stopped"),
  coolingHeading: assertEC12Clean("Proposals in cooling period"),
  coolingEmpty: assertEC12Clean(
    "No proposals are currently in their cooling period.",
  ),
  coolingConfirm: assertEC12Clean("Confirm"),
  coolingRevert: assertEC12Clean("Revert"),
  coolingRevertDialogTitle: assertEC12Clean("Revert this proposal?"),
  coolingRevertDialogBody: assertEC12Clean(
    "The inverse schema change will be applied immediately. This cannot be undone.",
  ),
  coolingRevertReasonLabel: assertEC12Clean("Reason for reverting"),
  coolingRevertReasonPlaceholder: assertEC12Clean("Describe why this change should be reverted"),
  coolingRevertCancel: assertEC12Clean("Cancel"),
  coolingRevertSubmit: assertEC12Clean("Revert proposal"),
  coolingRevertedBy: assertEC12Clean("Reverted by"),
  coolingTierLabel: assertEC12Clean("Tier"),
  coolingExpiresLabel: assertEC12Clean("Cooling expires"),
  coolingCommandLabel: assertEC12Clean("Change"),
  // Chunk 65 D446–D448 Governance Audit-Trail Hardening.
  killSwitchReasonPlaceholder: assertEC12Clean(
    "Describe why autonomous evolution is being stopped",
  ),
  killSwitchReasonLabel: assertEC12Clean("Reason"),
  restoreStateHeading: assertEC12Clean("Restore prior tier state"),
  restoreStateBody: assertEC12Clean(
    "Resuming autonomy will restore each tier to its state before the kill switch was engaged.",
  ),
  restoreStateTierEnabled: assertEC12Clean("will be re-enabled"),
  restoreStateTierDisabled: assertEC12Clean("will remain disabled"),
  restoreStateConfirm: assertEC12Clean("Resume and restore"),
  restoreStateCancel: assertEC12Clean("Cancel"),
  forceDisengageLabel: assertEC12Clean("Force disengage"),
} as const;

export type AutonomyCopyKey = keyof typeof AUTONOMY_COPY;
