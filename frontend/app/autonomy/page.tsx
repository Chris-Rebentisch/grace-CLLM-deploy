"use client";

/**
 * /autonomy — Earned Autonomy Calibration Surface (Chunk 49, CP8).
 *
 * Per-tier reliability bar chart, progress gate, three-band trust
 * indicator, and per-tier risk-tolerance config. Emits
 * `calibration_dashboard_viewed` telemetry on mount.
 *
 * D120/D217 — no numeric scores in DOM (trust_score, approval_rate,
 * sample_count are backend-only; bands and labels surface instead).
 * D195/EC-7 — no third-party CDN/telemetry hosts.
 * D194 — X-Graph-Scope: all header on every outbound API request.
 */

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api/client";
import type {
  CalibrationDashboardResponse,
  DaemonStatusResponse,
  TierDashboard,
  TrustScoreState,
} from "@/lib/api/types";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { CalibrationProgressBar } from "@/components/autonomy/CalibrationProgressBar";
import { CoolingProposalList } from "@/components/autonomy/CoolingProposalList";
import { KillSwitchButton } from "@/components/autonomy/KillSwitchButton";
import { ReliabilityChart } from "@/components/autonomy/ReliabilityChart";
import { RiskToleranceConfig } from "@/components/autonomy/RiskToleranceConfig";
import { TrustIndicatorBadge } from "@/components/autonomy/TrustIndicator";

const TIER_LABELS: Record<number, string> = {
  1: AUTONOMY_COPY.tierLabel1,
  2: AUTONOMY_COPY.tierLabel2,
  3: AUTONOMY_COPY.tierLabel3,
};

export default function AutonomyPage() {
  const [dashboard, setDashboard] =
    useState<CalibrationDashboardResponse | null>(null);
  const [daemonStatus, setDaemonStatus] =
    useState<DaemonStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const calData = await apiRequest<CalibrationDashboardResponse>(
          "/api/ontology/calibration/dashboard",
        );
        if (cancelled) return;
        setDashboard(calData);
        emitTelemetry("calibration_dashboard_viewed", {
          tiers_loaded: calData.tiers.length,
        });
        // Daemon status is best-effort — failures don't block the page.
        try {
          const statusData = await apiRequest<DaemonStatusResponse>(
            "/api/ontology/daemon/status",
          );
          if (!cancelled) setDaemonStatus(statusData);
        } catch {
          // Swallow — kill switch defaults to engaged (safe).
        }
      } catch (e) {
        if (!cancelled)
          setErr(
            e instanceof Error
              ? e.message
              : AUTONOMY_COPY.dashboardError,
          );
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleConfigUpdated = (
    tier: number,
    updated: TrustScoreState,
  ) => {
    if (!dashboard) return;
    setDashboard({
      tiers: dashboard.tiers.map((t) =>
        t.tier === tier ? { ...t, trust_score_state: updated } : t,
      ),
    });
  };

  return (
    <main
      data-testid="autonomy-page"
      className="mx-auto flex max-w-4xl flex-col gap-4 p-4"
    >
      <header>
        <h1 className="text-lg font-semibold text-slate-900">
          {AUTONOMY_COPY.pageTitle}
        </h1>
        <p className="mt-1 text-xs text-slate-500">
          {AUTONOMY_COPY.pageDescription}
        </p>
      </header>

      {err ? (
        <p
          data-testid="autonomy-page-error"
          className="rounded border border-rose-300 bg-rose-50 p-2 text-xs text-rose-700"
        >
          {err}
        </p>
      ) : null}

      {loading ? (
        <p className="text-xs text-slate-500">Loading\u2026</p>
      ) : dashboard ? (
        <div className="flex flex-col gap-6">
          {/* Kill switch section */}
          <section
            data-testid="kill-switch-section"
            className="rounded-md border border-slate-200 bg-white p-4"
          >
            <h2 className="mb-2 text-sm font-semibold text-slate-900">
              {AUTONOMY_COPY.killSwitchHeading}
            </h2>
            <KillSwitchButton
              engaged={daemonStatus?.kill_switch_engaged ?? true}
              previousState={daemonStatus?.previous_state ?? undefined}
              onToggled={(enabled) =>
                setDaemonStatus((prev) =>
                  prev
                    ? { ...prev, kill_switch_engaged: !enabled, previous_state: null }
                    : prev,
                )
              }
            />
          </section>

          {/* Cooling proposals section */}
          <section
            data-testid="cooling-section"
            className="rounded-md border border-slate-200 bg-white p-4"
          >
            <h2 className="mb-2 text-sm font-semibold text-slate-900">
              {AUTONOMY_COPY.coolingHeading}
            </h2>
            <CoolingProposalList />
          </section>

          {dashboard.tiers.map((tierData: TierDashboard) => (
            <TierSection
              key={tierData.tier}
              tierData={tierData}
              onConfigUpdated={(updated) =>
                handleConfigUpdated(tierData.tier, updated)
              }
            />
          ))}
        </div>
      ) : (
        <p
          data-testid="autonomy-no-data"
          className="text-xs italic text-slate-500"
        >
          {AUTONOMY_COPY.noData}
        </p>
      )}
    </main>
  );
}

function TierSection({
  tierData,
  onConfigUpdated,
}: {
  tierData: TierDashboard;
  onConfigUpdated: (updated: TrustScoreState) => void;
}) {
  return (
    <section
      data-testid={`tier-section-${tierData.tier}`}
      className="rounded-md border border-slate-200 bg-white p-4"
    >
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">
          {TIER_LABELS[tierData.tier] ?? `${AUTONOMY_COPY.tierHeading} ${tierData.tier}`}
        </h2>
        <TrustIndicatorBadge indicator={tierData.trust_indicator} />
      </div>

      {tierData.trust_score_state.regression_detected ? (
        <p
          data-testid={`regression-banner-${tierData.tier}`}
          className="mb-3 rounded border border-rose-300 bg-rose-50 p-2 text-[10px] text-rose-700"
        >
          {AUTONOMY_COPY.regressionBanner}
        </p>
      ) : null}

      <div className="mb-3">
        <h3 className="mb-1 text-[11px] font-medium text-slate-700">
          {AUTONOMY_COPY.progressHeading}
        </h3>
        <CalibrationProgressBar
          progress={tierData.progress}
          testId={`progress-bar-${tierData.tier}`}
        />
      </div>

      <div className="mb-3">
        <h3 className="mb-1 text-[11px] font-medium text-slate-700">
          {AUTONOMY_COPY.reliabilityHeading}
        </h3>
        <ReliabilityChart
          bands={tierData.bands}
          testId={`reliability-chart-${tierData.tier}`}
        />
      </div>

      <RiskToleranceConfig
        tier={tierData.tier}
        trustState={tierData.trust_score_state}
        onUpdated={onConfigUpdated}
      />
    </section>
  );
}
