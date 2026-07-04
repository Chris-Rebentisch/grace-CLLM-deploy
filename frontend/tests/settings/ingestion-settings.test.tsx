import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
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
];

let originalFetch: typeof globalThis.fetch;
const patchCalls: { url: string; body: unknown }[] = [];

function makeFetch() {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const method = (init?.method ?? "GET").toUpperCase();

    if (method === "GET" && url.endsWith("/api/llm/config")) {
      return new Response(JSON.stringify(llmConfig), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (method === "GET" && url.endsWith("/api/llm/registry")) {
      return new Response(JSON.stringify(registry), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (method === "GET" && url.endsWith("/api/ingestion/config")) {
      return new Response(
        JSON.stringify({
          deployment_path: "A",
          organization_domains: ["example.com"],
          tier3_band: "balanced",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (method === "GET" && url.endsWith("/api/communications/dpia/status")) {
      return new Response(
        JSON.stringify({
          attestation_active: false,
          valid_until: null,
          signed_by: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }

    if (method === "PATCH" && url.includes("/api/ingestion/config")) {
      const raw = init?.body ? String(init.body) : "{}";
      const body = JSON.parse(raw) as unknown;
      patchCalls.push({ url, body });
      if (url.endsWith("/api/ingestion/config/deployment-path")) {
        const deployment_path = (body as { deployment_path?: string | null })
          .deployment_path;
        return new Response(JSON.stringify({ deployment_path }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/api/ingestion/config/organization-domains")) {
        const organization_domains = (body as { organization_domains?: string[] })
          .organization_domains ?? [];
        return new Response(JSON.stringify({ organization_domains }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/api/ingestion/config/tier3-threshold")) {
        const tier3_band = (body as { tier3_band?: string }).tier3_band ?? "balanced";
        return new Response(JSON.stringify({ tier3_band }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
    }

    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  patchCalls.length = 0;
  useSettingsStore.setState({
    draft: null,
    baseSnapshot: null,
    airgapBreakDialogOpen: false,
  });
  originalFetch = globalThis.fetch;
  globalThis.fetch = makeFetch();
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function renderPanel() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SettingsPanel />
    </QueryClientProvider>,
  );
}

describe("SettingsPanel ingestion section", () => {
  it("hydrates settings-ingestion-section from GET /api/ingestion/config", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("settings-ingestion-section")).toBeTruthy();
    });
    const select = screen.getByTestId(
      "deployment-path-select",
    ) as HTMLSelectElement;
    expect(select.value).toBe("A");
    expect(screen.getAllByTestId("domain-chip").map((e) => e.textContent)).toContain(
      "example.com×",
    );
    expect(screen.getByTestId("tier3-band-balanced")).toHaveClass("border-blue-400");
    expect(screen.getByTestId("dpia-indicator")).toBeTruthy();
  });

  it("PATCHes deployment path when operator selects a new path", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("deployment-path-select")).toBeTruthy();
    });
    const select = screen.getByTestId("deployment-path-select");
    fireEvent.change(select, { target: { value: "B" } });
    await waitFor(() => {
      expect(
        patchCalls.some(
          (c) =>
            c.url.endsWith("/api/ingestion/config/deployment-path") &&
            (c.body as { deployment_path: string }).deployment_path === "B",
        ),
      ).toBe(true);
    });
  });

  it("PATCHes organization_domains when adding a domain", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("new-domain-input")).toBeTruthy();
    });
    fireEvent.change(screen.getByTestId("new-domain-input"), {
      target: { value: "corp.example" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => {
      const hit = patchCalls.find((c) =>
        c.url.endsWith("/api/ingestion/config/organization-domains"),
      );
      expect(hit).toBeTruthy();
      expect((hit!.body as { organization_domains: string[] }).organization_domains).toEqual(
        ["example.com", "corp.example"],
      );
    });
  });

  it("PATCHes tier3_band when operator selects stricter", async () => {
    renderPanel();
    await waitFor(() => {
      expect(screen.getByTestId("tier3-band-stricter")).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId("tier3-band-stricter"));
    await waitFor(() => {
      expect(
        patchCalls.some(
          (c) =>
            c.url.endsWith("/api/ingestion/config/tier3-threshold") &&
            (c.body as { tier3_band: string }).tier3_band === "stricter",
        ),
      ).toBe(true);
    });
  });
});
