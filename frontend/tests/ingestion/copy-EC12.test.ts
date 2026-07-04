import { describe, expect, it } from "vitest";

import {
  EC12_FORBIDDEN_TOKENS,
  INGESTION_COPY,
  assertEC12Clean,
} from "@/lib/ingestion/copy";

describe("ingestion copy discipline (EC-12)", () => {
  it("contains no forbidden tokens", () => {
    const corpus = Object.values(INGESTION_COPY).join(" ").toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(
        corpus.includes(token.toLowerCase()),
        `forbidden token "${token}" present in ingestion copy`,
      ).toBe(false);
    }
  });

  it("assertEC12Clean throws on every forbidden token", () => {
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(() => assertEC12Clean(`Sample ${token} message`)).toThrow(
        /EC-12 violation/,
      );
    }
    expect(assertEC12Clean("All clear")).toBe("All clear");
  });

  it("exposes the deployment-path labels", () => {
    expect(INGESTION_COPY.pathA).toBeTruthy();
    expect(INGESTION_COPY.pathB).toBeTruthy();
    expect(INGESTION_COPY.pathC).toBeTruthy();
  });

  it("exposes the seven source-type labels", () => {
    expect(INGESTION_COPY.sourceTypeMbox).toBeTruthy();
    expect(INGESTION_COPY.sourceTypeEml).toBeTruthy();
    expect(INGESTION_COPY.sourceTypeMsg).toBeTruthy();
    expect(INGESTION_COPY.sourceTypePst).toBeTruthy();
    expect(INGESTION_COPY.sourceTypeImap).toBeTruthy();
    expect(INGESTION_COPY.sourceTypeExchange).toBeTruthy();
    expect(INGESTION_COPY.sourceTypeGmail).toBeTruthy();
  });

  it("exposes the readiness gate labels", () => {
    expect(INGESTION_COPY.readinessHeading).toBeTruthy();
    expect(INGESTION_COPY.readinessReady).toBeTruthy();
    expect(INGESTION_COPY.readinessNotReady).toBeTruthy();
    expect(INGESTION_COPY.readinessBootstrapPending).toBeTruthy();
    expect(INGESTION_COPY.personCountLabel).toBeTruthy();
    expect(INGESTION_COPY.orgCountLabel).toBeTruthy();
    expect(INGESTION_COPY.cqCountLabel).toBeTruthy();
  });

  it("exposes the deferred badge label", () => {
    expect(INGESTION_COPY.deferredBadge).toBeTruthy();
  });

  it("exposes live source config labels (Chunk 57)", () => {
    expect(INGESTION_COPY.tenantIdLabel).toBeTruthy();
    expect(INGESTION_COPY.refreshTokenEnvLabel).toBeTruthy();
    expect(INGESTION_COPY.passwordLabel).toBeTruthy();
    expect(INGESTION_COPY.appPasswordEnvLabel).toBeTruthy();
    expect(INGESTION_COPY.graphUrlLabel).toBeTruthy();
    expect(INGESTION_COPY.usernameLabel).toBeTruthy();
  });

  it("exposes the curation labels (Chunk 56)", () => {
    expect(INGESTION_COPY.curatePageTitle).toBeTruthy();
    expect(INGESTION_COPY.curatePageDescription).toBeTruthy();
    expect(INGESTION_COPY.senderDiversityLabel).toBeTruthy();
    expect(INGESTION_COPY.threadDepthLabel).toBeTruthy();
    expect(INGESTION_COPY.dateRangeLabel).toBeTruthy();
    expect(INGESTION_COPY.curateButton).toBeTruthy();
    expect(INGESTION_COPY.selectAll).toBeTruthy();
    expect(INGESTION_COPY.deselectAll).toBeTruthy();
  });

  it("exposes sample-size advisory copy (Chunk 56 CP9)", () => {
    expect(INGESTION_COPY.sampleSizeWarningLow).toBeTruthy();
    expect(INGESTION_COPY.sampleSizeRepresentative).toBeTruthy();
    expect(INGESTION_COPY.sampleSizeWarningHigh).toBeTruthy();
  });

  it("exposes diversity preview heading (Chunk 56 CP9)", () => {
    expect(INGESTION_COPY.diversityPreviewHeading).toBeTruthy();
  });

  it("exposes thread depth v1 notice (Chunk 56 CP9)", () => {
    expect(INGESTION_COPY.threadDepthV1Notice).toContain("thread");
  });

  it("sample-size copy has no forbidden EC-12 tokens", () => {
    const sampleCorpus = [
      INGESTION_COPY.sampleSizeWarningLow,
      INGESTION_COPY.sampleSizeRepresentative,
      INGESTION_COPY.sampleSizeWarningHigh,
    ]
      .join(" ")
      .toLowerCase();
    for (const token of EC12_FORBIDDEN_TOKENS) {
      expect(sampleCorpus.includes(token.toLowerCase())).toBe(false);
    }
  });

  it("exposes OAuth copy entries (Chunk 57)", () => {
    expect(INGESTION_COPY.oauthHeading).toBeTruthy();
    expect(INGESTION_COPY.oauthInitButton).toBeTruthy();
    expect(INGESTION_COPY.oauthInstructions).toBeTruthy();
    expect(INGESTION_COPY.oauthPastePlaceholder).toBeTruthy();
    expect(INGESTION_COPY.oauthSubmitButton).toBeTruthy();
    expect(INGESTION_COPY.oauthSuccess).toBeTruthy();
  });

  it("exposes schedule config copy entries (Chunk 57)", () => {
    expect(INGESTION_COPY.scheduleHeading).toBeTruthy();
    expect(INGESTION_COPY.scheduleEnabledLabel).toBeTruthy();
    expect(INGESTION_COPY.scheduleModeLabel).toBeTruthy();
    expect(INGESTION_COPY.scheduleModeInterval).toBeTruthy();
    expect(INGESTION_COPY.scheduleModeOneTime).toBeTruthy();
    expect(INGESTION_COPY.scheduleIntervalLabel).toBeTruthy();
  });

  it("exposes dashboard copy entries (Chunk 60)", () => {
    expect(INGESTION_COPY.dashboardTitle).toBeTruthy();
    expect(INGESTION_COPY.dashboardEmptyState).toBeTruthy();
    expect(INGESTION_COPY.dashboardRefreshButton).toBeTruthy();
    expect(INGESTION_COPY.dashboardSourcesHeading).toBeTruthy();
    expect(INGESTION_COPY.dashboardRunsHeading).toBeTruthy();
    expect(INGESTION_COPY.dashboardTriageFunnelHeading).toBeTruthy();
    expect(INGESTION_COPY.dashboardFunnelBandNote).toBeTruthy();
    expect(INGESTION_COPY.dashboardReconsentNeeded).toBeTruthy();
  });

  it("exposes source detail copy entries (Chunk 60)", () => {
    expect(INGESTION_COPY.sourceDetailBackLink).toBeTruthy();
    expect(INGESTION_COPY.sourceDetailEventsHeading).toBeTruthy();
    expect(INGESTION_COPY.sourceDetailStatusHeading).toBeTruthy();
    expect(INGESTION_COPY.sourceDetailLoadMore).toBeTruthy();
    expect(INGESTION_COPY.sourceDetailNotFound).toBeTruthy();
    expect(INGESTION_COPY.sourceDetailReauthorize).toBeTruthy();
  });

  it("exposes settings integration copy entries (Chunk 60)", () => {
    expect(INGESTION_COPY.settingsIngestionHeading).toBeTruthy();
    expect(INGESTION_COPY.settingsDeploymentPathLabel).toBeTruthy();
    expect(INGESTION_COPY.settingsOrganizationDomainsLabel).toBeTruthy();
    expect(INGESTION_COPY.settingsAddDomainButton).toBeTruthy();
    expect(INGESTION_COPY.settingsTier3BandLabel).toBeTruthy();
    expect(INGESTION_COPY.settingsTier3Stricter).toBeTruthy();
    expect(INGESTION_COPY.settingsTier3Balanced).toBeTruthy();
    expect(INGESTION_COPY.settingsTier3Looser).toBeTruthy();
    expect(INGESTION_COPY.settingsDpiaIndicator).toBeTruthy();
  });

  it("Chunk 60 copy passes extended forbidden vocabulary", () => {
    const extendedForbidden = [
      "incorrect", "failure", "deficit", "drift", "blind spot",
      "mistake", "wrong", "bad", "unprofessional", "inappropriate",
    ];
    const chunk60Corpus = [
      INGESTION_COPY.dashboardTitle,
      INGESTION_COPY.dashboardEmptyState,
      INGESTION_COPY.sourceDetailNotFound,
      INGESTION_COPY.settingsTier3BandLabel,
    ].join(" ").toLowerCase();
    for (const token of extendedForbidden) {
      expect(chunk60Corpus).not.toContain(token.toLowerCase());
    }
  });
});
