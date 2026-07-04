/**
 * EC-12 compliant copy registry for the SupportSessionBanner (D375).
 *
 * No numeric confidence scores, no "accuracy"/"precision" language
 * (D120/D217). All strings are forbidden-token-clean.
 */

export const BANNER_COPY = {
  /** Persistent yellow chip label when a support session is active. */
  ACTIVE_LABEL: "Support session active",

  /** Detail line showing operator email. */
  OPERATOR_PREFIX: "Operator:",

  /** Detail line showing session expiry. */
  EXPIRES_PREFIX: "Expires:",

  /** Revoke button label (admin users). */
  REVOKE_BUTTON: "Revoke session",

  /** Non-admin fallback text. */
  CONTACT_ADMIN: "Contact your administrator to manage this session",

  /** Toast notification on first banner appearance. */
  TOAST_MESSAGE: "A remote support session is now active",

  /** Banner hidden state aria label. */
  HIDDEN_LABEL: "No active support session",
} as const;
