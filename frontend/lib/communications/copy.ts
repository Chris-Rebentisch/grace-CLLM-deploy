/**
 * EC-12 copy registry for Voice & Tone Profiling (Chunk 58, CP9).
 *
 * Every user-visible string in `frontend/app/communications/` and
 * `frontend/components/communications/` must come from this file
 * and pass `assertEC12Clean()`.
 */

import { EC12_FORBIDDEN_TOKENS, assertEC12Clean } from "@/lib/permissions/copy";

export { EC12_FORBIDDEN_TOKENS, assertEC12Clean };

export const COMMUNICATIONS_COPY = {
  pageTitle: assertEC12Clean("Voice & Tone DPIA settings"),
  pageDescription: assertEC12Clean(
    "Manage DPIA attestation for individual-mode profiling."
  ),
  dpiaActiveLabel: assertEC12Clean("Attestation active"),
  dpiaInactiveLabel: assertEC12Clean("No active attestation"),
  validUntilLabel: assertEC12Clean("Valid until"),
  signedByLabel: assertEC12Clean("Signed by"),
  signForm: assertEC12Clean("Submit DPIA attestation"),
  nameField: assertEC12Clean("Signer name"),
  roleField: assertEC12Clean("Signer role"),
  dateField: assertEC12Clean("Signing date"),
  submitButton: assertEC12Clean("Submit attestation"),
  successMessage: assertEC12Clean("Attestation submitted successfully"),
  duplicateError: assertEC12Clean(
    "An attestation already exists for today. Try again tomorrow."
  ),
  templateChangedError: assertEC12Clean(
    "The DPIA template has changed since you loaded the page. Please reload and re-sign."
  ),
  modeAggregate: assertEC12Clean("Aggregate mode"),
  modeIndividual: assertEC12Clean("Individual mode (requires DPIA)"),
  navLink: assertEC12Clean("DPIA settings"),
  // Profile browser (Chunk 60, CP5)
  profileListTitle: assertEC12Clean("Communication profiles"),
  profileListSearchPlaceholder: assertEC12Clean("Search by person ID"),
  profileListEmpty: assertEC12Clean("No profiles found."),
  profileDetailBackLink: assertEC12Clean("Profiles"),
  profileDetailStyleSignatureHeading: assertEC12Clean("Style signature"),
  profileDetailRecipientsHeading: assertEC12Clean("Recipients by category"),
  profileDetailProvisionalAdvisory: assertEC12Clean(
    "provisional — limited data"
  ),
  profileAggregateBackLink: assertEC12Clean("Profiles"),
  profileAggregatePrefix: assertEC12Clean("Aggregate"),
} as const;

export type CommunicationsCopyKey = keyof typeof COMMUNICATIONS_COPY;
