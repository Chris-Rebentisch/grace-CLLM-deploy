import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProviderConfigForm } from "@/components/settings/ProviderConfigForm";
import { AirgapBreakConfirmDialog } from "@/components/settings/AirgapBreakConfirmDialog";
import { useSettingsStore } from "@/lib/state/settings-store";
import { onTelemetry, clearRecentTelemetry } from "@/lib/telemetry/bus";

const registry = [
  {
    id: "ollama",
    label: "Ollama",
    description: "local",
    requires_api_key: false,
    requires_base_url: true,
    default_model: "qwen2.5:7b",
    default_base_url: "http://localhost:11434",
    popular_models: [],
  },
  {
    id: "anthropic",
    label: "Anthropic",
    description: "cloud",
    requires_api_key: true,
    requires_base_url: false,
    default_model: "claude-haiku-4-5-20251001",
    default_base_url: "",
    popular_models: [],
  },
];

let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  clearRecentTelemetry();
  useSettingsStore.setState({
    draft: {
      provider: "anthropic",
      model: "claude-haiku-4-5-20251001",
      base_url: "",
      timeout: 60,
      api_key: "secret",
      airgap_mode: true, // airgap ON + cloud provider = should trigger dialog
    },
    baseSnapshot: {
      provider: "ollama",
      model: "qwen2.5:7b",
      base_url: "http://localhost:11434",
      timeout: 60,
      api_key: "",
      airgap_mode: true,
    },
    airgapBreakDialogOpen: false,
  });
  originalFetch = globalThis.fetch;
  globalThis.fetch = (async (url: string) => {
    if (typeof url === "string" && url.endsWith("/api/llm/registry")) {
      return new Response(JSON.stringify(registry), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.endsWith("/api/llm/config")) {
      return new Response(
        JSON.stringify({
          provider: "anthropic",
          model: "claude-haiku-4-5-20251001",
          base_url: "",
          timeout: 60,
          api_key_set: true,
          api_key_preview: "***",
          airgap_mode: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

describe("Airgap save round-trip (D232)", () => {
  it("Save with airgap+cloud-provider opens dialog; dialog action flips airgap off and emits airgap_mode_toggled", async () => {
    const seen: { type: string; payload?: Record<string, unknown> }[] = [];
    const unsub = onTelemetry((e) =>
      seen.push({ type: e.type, payload: e.payload }),
    );
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <ProviderConfigForm />
        <AirgapBreakConfirmDialog />
      </QueryClientProvider>,
    );
    // Wait for registry to load.
    await waitFor(() => {
      expect(screen.getByTestId("provider-config-form")).toBeTruthy();
    });
    // Click Save -> dialog should open (airgap ON + provider requires API key).
    fireEvent.click(screen.getByTestId("save-button"));
    await waitFor(() => {
      expect(screen.getByTestId("airgap-break-dialog")).toBeTruthy();
    });
    // Click "Disable airgap mode" -> flips airgap, emits airgap_mode_toggled.
    fireEvent.click(screen.getByTestId("airgap-break-disable"));
    expect(useSettingsStore.getState().draft?.airgap_mode).toBe(false);
    const evt = seen.find((e) => e.type === "airgap_mode_toggled");
    expect(evt).toBeTruthy();
    expect(evt?.payload?.enabled).toBe(false);
    unsub();
  });
});
