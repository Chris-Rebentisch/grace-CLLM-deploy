/**
 * EC-12 forbidden-vocabulary-clean copy registry for Communication
 * Ingestion setup surface (Chunk 55, D419/D420/D427).
 *
 * Every user-facing string for `frontend/app/ingestion/**` and
 * `frontend/components/ingestion/**` must come through this module.
 * `assertEC12Clean` runs at module load — a forbidden-token leak
 * fails the import.
 *
 * D120/D217: readiness diagnostics (person_count, organization_count,
 * accepted_cq_count) are operational-exempt per research §10.2.
 */
import {
  EC12_FORBIDDEN_TOKENS,
  assertEC12Clean,
} from "@/lib/permissions/copy";

export { EC12_FORBIDDEN_TOKENS, assertEC12Clean };

export const INGESTION_COPY = {
  pageTitle: assertEC12Clean("Communication ingestion setup"),
  pageDescription: assertEC12Clean(
    "Configure ingestion sources, select a deployment path, and check readiness before running.",
  ),

  // Path selector
  pathSelectorHeading: assertEC12Clean("Deployment path"),
  pathA: assertEC12Clean("Path A — Direct ingestion"),
  pathB: assertEC12Clean("Path B — Bootstrapped ingestion"),
  pathC: assertEC12Clean("Path C — Curated ingestion"),

  // Source types
  sourceTypeHeading: assertEC12Clean("Source type"),
  sourceTypeMbox: assertEC12Clean("Mbox file"),
  sourceTypeEml: assertEC12Clean("EML directory"),
  sourceTypeMsg: assertEC12Clean("Outlook MSG"),
  sourceTypePst: assertEC12Clean("PST archive"),
  sourceTypeImap: assertEC12Clean("IMAP"),
  sourceTypeExchange: assertEC12Clean("Exchange"),
  sourceTypeGmail: assertEC12Clean("Gmail"),
  deferredBadge: assertEC12Clean("Coming in a later chunk"),

  // Config form
  configFormHeading: assertEC12Clean("Source configuration"),
  filePathLabel: assertEC12Clean("File path"),
  directoryPathLabel: assertEC12Clean("Directory path"),
  hostLabel: assertEC12Clean("Server host"),
  passwordLabel: assertEC12Clean("Password"),
  appPasswordEnvLabel: assertEC12Clean("App password environment variable"),
  tenantIdLabel: assertEC12Clean("Azure AD tenant ID"),
  refreshTokenEnvLabel: assertEC12Clean("Refresh token environment variable"),
  graphUrlLabel: assertEC12Clean("Microsoft Graph base URL"),
  usernameLabel: assertEC12Clean("Username or mailbox"),
  segmentLabel: assertEC12Clean("Ontology module / segment"),

  // Test connection
  testConnectionButton: assertEC12Clean("Test connection"),
  testConnectionSuccess: assertEC12Clean("Connection test passed"),
  testConnectionFailure: assertEC12Clean("Connection test failed"),

  // Readiness gate
  readinessHeading: assertEC12Clean("Readiness gate"),
  readinessReady: assertEC12Clean("Ready"),
  readinessNotReady: assertEC12Clean("Not ready"),
  readinessBootstrapPending: assertEC12Clean("Bootstrap pending"),
  personCountLabel: assertEC12Clean("Person entities"),
  orgCountLabel: assertEC12Clean("Organization entities"),
  cqCountLabel: assertEC12Clean("Accepted CQs"),

  // Trigger
  triggerButton: assertEC12Clean("Start ingestion run"),
  triggerSuccess: assertEC12Clean("Ingestion run started"),

  // Curation (Chunk 56, D432)
  curatePageTitle: assertEC12Clean("Email curation"),
  curatePageDescription: assertEC12Clean(
    "Select emails for Path B or C bootstrapped curation.",
  ),
  sourceIdLabel: assertEC12Clean("Source ID"),
  deploymentPathLabel: assertEC12Clean("Deployment path"),
  loadEventsButton: assertEC12Clean("Load events"),
  emailListHeading: assertEC12Clean("Available emails"),
  selectAll: assertEC12Clean("Select all"),
  deselectAll: assertEC12Clean("Deselect all"),
  selectedCount: assertEC12Clean("selected"),
  curateButton: assertEC12Clean("Curate selection"),
  curateSuccess: assertEC12Clean("Curation complete"),
  messagesLabel: assertEC12Clean("messages"),
  senderDiversityLabel: assertEC12Clean("Sender diversity"),
  threadDepthLabel: assertEC12Clean("Thread depth"),
  dateRangeLabel: assertEC12Clean("Date range"),
  diversityPreviewHeading: assertEC12Clean("Selection diversity preview"),
  threadDepthV1Notice: assertEC12Clean(
    "Thread depth bands use a single-thread placeholder until thread reconstruction ships.",
  ),
  sampleSizeWarningLow: assertEC12Clean(
    "Consider selecting more emails for a representative sample",
  ),
  sampleSizeRepresentative: assertEC12Clean("Representative sample"),
  sampleSizeWarningHigh: assertEC12Clean(
    "Larger selection may increase Discovery runtime without proportional gain",
  ),

  // OAuth2 (Chunk 57)
  oauthHeading: assertEC12Clean("OAuth consent"),
  oauthInitButton: assertEC12Clean("Start OAuth flow"),
  oauthInstructions: assertEC12Clean(
    "Open the link below to authorize access, then paste the callback URL.",
  ),
  oauthPastePlaceholder: assertEC12Clean("Paste the full callback URL here"),
  oauthSubmitButton: assertEC12Clean("Submit authorization"),
  oauthSuccess: assertEC12Clean("Authorization complete"),
  oauthInitFailed: assertEC12Clean("Failed to start OAuth flow"),
  oauthCallbackFailed: assertEC12Clean("Authorization callback failed"),
  oauthStateExpired: assertEC12Clean("OAuth state expired — please restart the flow"),
  oauthInvalidUrl: assertEC12Clean("Invalid callback URL format"),
  oauthNoCode: assertEC12Clean("No authorization code found in URL"),

  // Schedule (Chunk 57)
  scheduleHeading: assertEC12Clean("Ingestion schedule"),
  scheduleEnabledLabel: assertEC12Clean("Enable scheduled ingestion"),
  scheduleModeLabel: assertEC12Clean("Schedule mode"),
  scheduleModeInterval: assertEC12Clean("Recurring interval"),
  scheduleModeOneTime: assertEC12Clean("One-time run"),
  scheduleIntervalLabel: assertEC12Clean("Interval (hours)"),

  // Dashboard (Chunk 60, CP3)
  dashboardTitle: assertEC12Clean("Ingestion"),
  dashboardEmptyState: assertEC12Clean("No ingestion runs yet."),
  dashboardRefreshButton: assertEC12Clean("Refresh"),
  dashboardSourcesHeading: assertEC12Clean("Sources"),
  dashboardRunsHeading: assertEC12Clean("Runs"),
  dashboardTriageFunnelHeading: assertEC12Clean("Triage funnel"),
  dashboardFunnelBandNote: assertEC12Clean("Band labels only"),
  dashboardReconsentNeeded: assertEC12Clean("re-consent needed"),

  // Source detail (Chunk 60, CP4)
  sourceDetailBackLink: assertEC12Clean("Dashboard"),
  sourceDetailEventsHeading: assertEC12Clean("Events"),
  sourceDetailStatusHeading: assertEC12Clean("Source status"),
  sourceDetailLoadMore: assertEC12Clean("Load more"),
  sourceDetailNotFound: assertEC12Clean("Source not found"),
  sourceDetailReauthorize: assertEC12Clean("Re-authorize"),

  // Settings integration (Chunk 60, CP7)
  settingsIngestionHeading: assertEC12Clean("Ingestion"),
  settingsDeploymentPathLabel: assertEC12Clean("Deployment path"),
  settingsOrganizationDomainsLabel: assertEC12Clean("Organization domains"),
  settingsAddDomainButton: assertEC12Clean("Add"),
  settingsRemoveDomainLabel: assertEC12Clean("Remove"),
  settingsTier3BandLabel: assertEC12Clean("Tier 3 threshold band"),
  settingsTier3Stricter: assertEC12Clean("Stricter"),
  settingsTier3Balanced: assertEC12Clean("Balanced"),
  settingsTier3Looser: assertEC12Clean("Looser"),
  settingsDpiaIndicator: assertEC12Clean("DPIA attestation"),
} as const;
