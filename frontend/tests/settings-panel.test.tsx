import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { useSettingsStore } from "@/lib/state/settings-store";

const llmConfig = {
  provider: "ollama",
  model: "qwen2.5:7b",
  base_url: "http://localhost:11434",
  timeout: 60,
  api_key_set: false,
  api_key_preview: "",
  airgap_mode: true,
};

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
  useSettingsStore.setState({
    draft: null,
    baseSnapshot: null,
    airgapBreakDialogOpen: false,
  });
  originalFetch = globalThis.fetch;
  globalThis.fetch = (async (url: string) => {
    if (typeof url === "string" && url.endsWith("/api/llm/config")) {
      return new Response(JSON.stringify(llmConfig), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.endsWith("/api/llm/registry")) {
      return new Response(JSON.stringify(registry), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.endsWith("/api/ingestion/config")) {
      return new Response(
        JSON.stringify({ deployment_path: "A", organization_domains: ["example.com"], tier3_band: "balanced" }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (typeof url === "string" && url.endsWith("/api/communications/dpia/status")) {
      return new Response(
        JSON.stringify({ attestation_active: false, valid_until: null, signed_by: null }),
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

describe("SettingsPanel", () => {
  it("loads config, initializes draft, and renders provider/airgap surfaces", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <SettingsPanel />
      </QueryClientProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId("settings-panel")).toBeTruthy();
    });
    // AirgapToggle and ProviderConfigForm both rendered.
    expect(screen.getByTestId("airgap-toggle")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByTestId("provider-config-form")).toBeTruthy();
    });
    // Draft initialized from server snapshot.
    const draft = useSettingsStore.getState().draft;
    expect(draft?.provider).toBe("ollama");
    expect(draft?.airgap_mode).toBe(true);
  });
});
