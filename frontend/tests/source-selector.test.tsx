import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { SourceSelector } from "@/components/sources/SourceSelector";
import { useSessionStore } from "@/lib/state/session-store";
import { startTelemetryBridge } from "@/lib/telemetry/bridge";
import { onTelemetry, clearRecentTelemetry } from "@/lib/telemetry/bus";

const browseResponse = {
  path: "/data",
  parent: "/",
  entries: [
    { name: "claims", path: "/data/claims", is_dir: true, size_bytes: 0, supported: false },
    {
      name: "contract.pdf",
      path: "/data/contract.pdf",
      is_dir: false,
      size_bytes: 1200,
      supported: true,
    },
    { name: "notes.xyz", path: "/data/notes.xyz", is_dir: false, size_bytes: 50, supported: false },
  ],
};

const configureResponse = {
  manifest_path: "/data/manifest.json",
  total_files: 14,
  by_extension: { ".pdf": 14 },
  estimated_processing_minutes: 3,
};

let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  clearRecentTelemetry();
  useSessionStore.getState().clearSession();
  originalFetch = globalThis.fetch;
  globalThis.fetch = (async (url: string, _init?: RequestInit) => {
    if (typeof url === "string" && url.includes("/api/discovery/browse")) {
      return new Response(JSON.stringify(browseResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.includes("/api/discovery/configure-sources")) {
      return new Response(JSON.stringify(configureResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.includes("/api/discovery/process")) {
      return new Response(JSON.stringify({ status: "started", message: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.includes("/api/discovery/status")) {
      return new Response(JSON.stringify({ by_status: { processed: 14 } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (typeof url === "string" && url.includes("/api/elicitation/events")) {
      return new Response(JSON.stringify({ event_id: "ok", accepted_at: "now" }), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("{}", { status: 200 });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
  useSessionStore.getState().clearSession();
});

describe("SourceSelector", () => {
  it("browses files, selects a file, previews, confirms, then can start processing", async () => {
    useSessionStore.getState().startSession("open");
    const seen: string[] = [];
    const unsubBus = onTelemetry((e) => seen.push(e.type));
    const unsubBridge = startTelemetryBridge();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <SourceSelector />
      </QueryClientProvider>,
    );

    // File browser renders folders + files from /api/discovery/browse.
    const fileCheckbox = await screen.findByTestId("browse-checkbox-contract.pdf");
    // Unsupported file's checkbox is disabled.
    expect(
      (screen.getByTestId("browse-checkbox-notes.xyz") as HTMLInputElement).disabled,
    ).toBe(true);

    // Select the supported file -> Continue enables with "1 selected".
    fireEvent.click(fileCheckbox);
    await waitFor(() => {
      const submit = screen.getByTestId("sources-submit") as HTMLButtonElement;
      expect(submit.disabled).toBe(false);
      expect(submit.textContent).toMatch(/1 selected/);
    });

    // Submit -> opens the confirm modal with the configure preview.
    fireEvent.click(screen.getByTestId("sources-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("sources-confirm-modal")).toBeTruthy();
    });
    expect(screen.getByTestId("confirm-file-count").textContent).toBe("14");

    // Confirm -> emits sources_configured AND reveals the processing panel.
    fireEvent.click(screen.getByTestId("confirm-accept"));
    expect(seen).toContain("sources_configured");
    const startBtn = await screen.findByTestId("start-processing");

    // Start processing -> progress panel appears and polls status.
    fireEvent.click(startBtn);
    await waitFor(() => {
      expect(screen.getByTestId("processing-progress")).toBeTruthy();
    });

    unsubBus();
    unsubBridge();
  });
});
