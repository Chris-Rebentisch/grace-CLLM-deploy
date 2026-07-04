"use client";
import { useEffect, useState } from "react";
import type { TestLLMConfigResponse } from "@/lib/api/types";
import {
  useProviderRegistry,
  useSaveLLMConfig,
  useTestLLMConfig,
} from "@/lib/query/llm-config";
import { useSettingsStore } from "@/lib/state/settings-store";
import { useSessionStore } from "@/lib/state/session-store";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { ConnectionTestResult } from "./ConnectionTestResult";

export function ProviderConfigForm() {
  const { data: registry } = useProviderRegistry();
  const draft = useSettingsStore((s) => s.draft);
  const patch = useSettingsStore((s) => s.patchDraft);
  const baseSnapshot = useSettingsStore((s) => s.baseSnapshot);
  const openAirgapDialog = useSettingsStore((s) => s.openAirgapBreakDialog);
  const sessionId = useSessionStore((s) => s.sessionId);
  const phase = useSessionStore((s) => s.activePhase);

  const saveMut = useSaveLLMConfig();
  const testMut = useTestLLMConfig();
  const [testResult, setTestResult] = useState<TestLLMConfigResponse | null>(null);

  useEffect(() => {
    if (!draft || !registry) return;
    // No-op: hook present so future provider-change side-effects can attach here.
  }, [draft, registry]);

  if (!draft || !registry) return null;

  const selectedProvider = registry.find((p) => p.id === draft.provider);

  const handleSave = async () => {
    // D232 defense-in-depth: if airgap is on AND provider requires API key,
    // surface the dialog instead of posting (the backend also 422s).
    if (draft.airgap_mode && selectedProvider?.requires_api_key) {
      openAirgapDialog();
      return;
    }
    const priorProvider = baseSnapshot?.provider ?? draft.provider;
    await saveMut.mutateAsync({
      provider: draft.provider,
      model: draft.model,
      base_url: draft.base_url,
      timeout: draft.timeout,
      api_key: draft.api_key || null,
      airgap_mode: draft.airgap_mode,
    });
    if (priorProvider !== draft.provider) {
      emitTelemetry("llm_provider_switched", {
        from_provider_id: priorProvider,
        to_provider_id: draft.provider,
        airgap_mode_after: draft.airgap_mode,
      });
    }
    void sessionId;
    void phase;
  };

  const handleTest = async () => {
    const r = await testMut.mutateAsync({
      provider: draft.provider,
      model: draft.model,
      base_url: draft.base_url,
      timeout: draft.timeout,
      api_key: draft.api_key,
    });
    setTestResult(r);
  };

  return (
    <form
      data-testid="provider-config-form"
      className="space-y-2 rounded border bg-white p-3 text-xs"
      onSubmit={(e) => {
        e.preventDefault();
        void handleSave();
      }}
    >
      <label className="block">
        Provider
        <select
          data-testid="provider-select"
          className="mt-1 w-full rounded border px-2 py-1"
          value={draft.provider}
          onChange={(e) => {
            const id = e.target.value;
            const p = registry.find((r) => r.id === id);
            patch({
              provider: id,
              model: p?.default_model ?? draft.model,
              base_url: p?.default_base_url ?? draft.base_url,
            });
          }}
        >
          {registry.map((p) => (
            <option key={p.id} value={p.id}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block">
        Model
        <input
          data-testid="model-input"
          className="mt-1 w-full rounded border px-2 py-1"
          value={draft.model}
          onChange={(e) => patch({ model: e.target.value })}
        />
      </label>
      <label className="block">
        Base URL
        <input
          data-testid="base-url-input"
          className="mt-1 w-full rounded border px-2 py-1"
          value={draft.base_url}
          onChange={(e) => patch({ base_url: e.target.value })}
        />
      </label>
      {selectedProvider?.requires_api_key && (
        <label className="block">
          API key
          <input
            type="password"
            data-testid="api-key-input"
            className="mt-1 w-full rounded border px-2 py-1"
            value={draft.api_key}
            onChange={(e) => patch({ api_key: e.target.value })}
            placeholder="Leave blank to keep existing key"
          />
        </label>
      )}
      <div className="flex gap-2">
        <button
          type="submit"
          data-testid="save-button"
          disabled={saveMut.isPending}
          className="rounded bg-emerald-700 px-3 py-1 text-white"
        >
          Save
        </button>
        <button
          type="button"
          data-testid="test-button"
          disabled={testMut.isPending}
          onClick={() => void handleTest()}
          className="rounded border px-3 py-1"
        >
          Test connection
        </button>
      </div>
      <ConnectionTestResult result={testResult} />
    </form>
  );
}
