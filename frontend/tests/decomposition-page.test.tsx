// Chunk 41 D328 — /decomposition list page: render, polling, status badges.

import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";

vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

import DecompositionListPage from "@/app/decomposition/page";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function fetchReturning(payload: unknown, calls: string[] = []) {
  return (async (url: string) => {
    calls.push(url);
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

describe("DecompositionListPage (Chunk 41 D328)", () => {
  it("renders one row per run with a status badge", async () => {
    globalThis.fetch = fetchReturning({
      runs: [
        {
          run_id: "run-1",
          archive_root: "/tmp/a",
          archive_root_canonical_hash: "h1",
          status: "completed",
          triggered_at: "2026-05-08T00:00:00Z",
          completed_at: "2026-05-08T00:01:00Z",
        },
        {
          run_id: "run-2",
          archive_root: "/tmp/b",
          archive_root_canonical_hash: "h2",
          status: "running",
          triggered_at: "2026-05-08T00:00:00Z",
          completed_at: null,
        },
      ],
      next_cursor: null,
    });

    render(<DecompositionListPage />);
    await waitFor(() => {
      expect(screen.getByTestId("decomposition-run-row-run-1")).toBeTruthy();
      expect(screen.getByTestId("decomposition-run-row-run-2")).toBeTruthy();
    });
    expect(screen.getByTestId("decomposition-status-badge-run-1").textContent).toBe(
      "completed",
    );
    expect(screen.getByTestId("decomposition-status-badge-run-2").textContent).toBe(
      "running",
    );
  });

  it("polls the endpoint while a run is running", async () => {
    const calls: string[] = [];
    globalThis.fetch = fetchReturning(
      {
        runs: [
          {
            run_id: "rA",
            archive_root: "/x",
            archive_root_canonical_hash: "h",
            status: "running",
            triggered_at: "2026-05-08T00:00:00Z",
            completed_at: null,
          },
        ],
        next_cursor: null,
      },
      calls,
    );
    render(<DecompositionListPage />);
    // Initial render and effect-triggered load.
    await waitFor(() =>
      expect(screen.getByTestId("decomposition-run-row-rA")).toBeTruthy(),
    );
    const initial = calls.length;
    // Wait for the next poll tick (~3000ms). Use real timers + a 5s window.
    await new Promise((r) => setTimeout(r, 3500));
    await act(async () => {});
    expect(calls.length).toBeGreaterThan(initial);
  }, 10_000);

  it("renders the empty-state when no runs are returned", async () => {
    globalThis.fetch = fetchReturning({ runs: [], next_cursor: null });
    render(<DecompositionListPage />);
    await waitFor(() => {
      const page = screen.getByTestId("decomposition-list-page");
      expect(page.textContent).toContain("No decomposition runs yet.");
    });
  });
});
