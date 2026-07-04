"use client";
import { useCallback, useEffect, useState } from "react";
import { useLLMConfig } from "@/lib/query/llm-config";
import { useSettingsStore } from "@/lib/state/settings-store";
import { AirgapToggle } from "./AirgapToggle";
import { ProviderConfigForm } from "./ProviderConfigForm";
import { AirgapBreakConfirmDialog } from "./AirgapBreakConfirmDialog";
import {
  fetchIngestionConfig,
  patchOrganizationDomains,
  patchTier3Threshold,
  patchDeploymentPath,
  type IngestionConfig,
} from "@/lib/api/ingestion";
import { fetchDpiaStatus, type DpiaStatus } from "@/lib/api/communications";
import { postElicitationEvent } from "@/lib/telemetry/emit";
import { buildEnvelope } from "@/lib/telemetry/events";
import { useSessionStore } from "@/lib/state/session-store";

export function SettingsPanel() {
  const { data, isLoading, isError } = useLLMConfig();
  const initDraft = useSettingsStore((s) => s.initDraft);
  const draft = useSettingsStore((s) => s.draft);

  // Ingestion config state (Chunk 60, CP7)
  const [ingestionConfig, setIngestionConfig] = useState<IngestionConfig | null>(null);
  const [dpiaStatus, setDpiaStatus] = useState<DpiaStatus | null>(null);
  const [newDomain, setNewDomain] = useState("");
  const sessionId = useSessionStore((s) => s.sessionId);

  useEffect(() => {
    if (!data) return;
    initDraft({
      provider: data.provider,
      model: data.model,
      base_url: data.base_url,
      timeout: data.timeout,
      api_key: "",
      airgap_mode: data.airgap_mode,
    });
  }, [data, initDraft]);

  // Hydrate ingestion config on mount
  useEffect(() => {
    void fetchIngestionConfig()
      .then(setIngestionConfig)
      .catch(() => {});
    void fetchDpiaStatus()
      .then(setDpiaStatus)
      .catch(() => {});
  }, []);

  const emitSettingsChanged = useCallback(
    (key: "deployment_path" | "organization_domains" | "tier3_band") => {
      if (!sessionId) return;
      void postElicitationEvent(
        buildEnvelope({
          session_id: sessionId,
          phase_name: "none",
          event_type: "ingestion_settings_changed",
          payload: { setting_key: key },
        }),
      );
    },
    [sessionId],
  );

  const handleDeploymentPathChange = async (path: string) => {
    try {
      const res = await patchDeploymentPath(path);
      setIngestionConfig((prev) =>
        prev ? { ...prev, deployment_path: res.deployment_path } : prev,
      );
      emitSettingsChanged("deployment_path");
    } catch {
      // Silently handle
    }
  };

  const handleAddDomain = async () => {
    if (!newDomain.trim() || !ingestionConfig) return;
    const updated = [...ingestionConfig.organization_domains, newDomain.trim()];
    try {
      const res = await patchOrganizationDomains(updated);
      setIngestionConfig((prev) =>
        prev
          ? { ...prev, organization_domains: res.organization_domains }
          : prev,
      );
      setNewDomain("");
      emitSettingsChanged("organization_domains");
    } catch {
      // Silently handle
    }
  };

  const handleRemoveDomain = async (domain: string) => {
    if (!ingestionConfig) return;
    const updated = ingestionConfig.organization_domains.filter(
      (d) => d !== domain,
    );
    try {
      const res = await patchOrganizationDomains(updated);
      setIngestionConfig((prev) =>
        prev
          ? { ...prev, organization_domains: res.organization_domains }
          : prev,
      );
      emitSettingsChanged("organization_domains");
    } catch {
      // Silently handle
    }
  };

  const handleTier3Change = async (band: "stricter" | "balanced" | "looser") => {
    try {
      await patchTier3Threshold(band);
      setIngestionConfig((prev) =>
        prev ? { ...prev, tier3_band: band } : prev,
      );
      emitSettingsChanged("tier3_band");
    } catch {
      // Silently handle
    }
  };

  if (isLoading) {
    return (
      <p className="p-4 text-sm text-slate-500" data-testid="settings-loading">
        Loading settings…
      </p>
    );
  }
  if (isError) {
    return (
      <p className="p-4 text-sm text-rose-700" data-testid="settings-error">
        Failed to load settings.
      </p>
    );
  }
  if (!draft) return null;

  return (
    <div data-testid="settings-panel" className="space-y-3 p-4">
      <h1 className="text-lg font-semibold">LLM configuration</h1>
      <AirgapToggle />
      <ProviderConfigForm />
      <AirgapBreakConfirmDialog />

      {/* Ingestion section (Chunk 60, CP7) */}
      <section data-testid="settings-ingestion-section" className="border-t border-slate-200 pt-4">
        <h2 className="mb-2 text-base font-semibold">Ingestion</h2>

        {ingestionConfig && (
          <div className="space-y-3 text-sm">
            {/* Deployment path */}
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Deployment path
              </label>
              <select
                value={ingestionConfig.deployment_path ?? ""}
                onChange={(e) => void handleDeploymentPathChange(e.target.value)}
                className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm"
                data-testid="deployment-path-select"
              >
                <option value="">Not set</option>
                <option value="A">A — Archive ingestion</option>
                <option value="B">B — Empty-graph bootstrap</option>
                <option value="C">C — Document + email supplement</option>
              </select>
            </div>

            {/* Organization domains */}
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Organization domains
              </label>
              <div className="mt-1 flex flex-wrap gap-1">
                {(ingestionConfig.organization_domains ?? []).map((d) => (
                  <span
                    key={d}
                    className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 text-xs"
                    data-testid="domain-chip"
                  >
                    {d}
                    <button
                      type="button"
                      onClick={() => void handleRemoveDomain(d)}
                      className="text-slate-400 hover:text-slate-600"
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
              <div className="mt-1 flex gap-1">
                <input
                  type="text"
                  value={newDomain}
                  onChange={(e) => setNewDomain(e.target.value)}
                  placeholder="example.com"
                  className="rounded border border-slate-300 px-2 py-1 text-xs"
                  data-testid="new-domain-input"
                />
                <button
                  type="button"
                  onClick={() => void handleAddDomain()}
                  className="rounded border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50"
                >
                  Add
                </button>
              </div>
              <p className="mt-1 text-xs text-slate-400">
                Changes take effect on next scheduled sensitivity-tagger run.
              </p>
            </div>

            {/* Tier-3 threshold (band labels only — D120/D217) */}
            <div>
              <label className="block text-xs font-medium text-slate-600">
                Tier-3 semantic filter
              </label>
              <div className="mt-1 flex gap-2" data-testid="tier3-band-control">
                {(["stricter", "balanced", "looser"] as const).map((band) => (
                  <button
                    key={band}
                    type="button"
                    onClick={() => void handleTier3Change(band)}
                    className={`rounded border px-3 py-1 text-xs ${
                      ingestionConfig.tier3_band === band
                        ? "border-blue-400 bg-blue-50 font-medium text-blue-800"
                        : "border-slate-300 text-slate-600 hover:bg-slate-50"
                    }`}
                    data-testid={`tier3-band-${band}`}
                  >
                    {band}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* DPIA indicator */}
        {dpiaStatus && (
          <div className="mt-3 text-xs text-slate-500" data-testid="dpia-indicator">
            DPIA: {dpiaStatus.attestation_active ? (
              <span className="text-emerald-600">
                Active (valid until {dpiaStatus.valid_until})
              </span>
            ) : (
              <span className="text-amber-600">Not attested</span>
            )}
          </div>
        )}
      </section>
    </div>
  );
}
