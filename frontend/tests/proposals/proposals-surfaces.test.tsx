// Chunk 47 — list/detail proposals UI (CP7 remediation: filters, evidence, defer).
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("next/link", () => ({
  default: ({
    href,
    children,
    ...rest
  }: {
    href: string;
    children: React.ReactNode;
  }) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ proposal_id: "p1" }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/state/session-store", () => ({
  useSessionStore: (sel: (s: { sessionId: null }) => unknown) =>
    sel({ sessionId: null }),
}));

import ProposalsListPage from "@/app/proposals/page";
import ProposalDetailPage from "@/app/proposals/[proposal_id]/page";
import { PROPOSALS_COPY } from "@/lib/proposals/copy";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const sampleProposal = {
  id: "p1",
  created_at: "2026-05-13T00:00:00Z",
  proposal_type: "add_entity_type",
  change_tier: 2,
  kgcl_command: "create class X",
  proposed_diff: {},
  evidence: {
    source_signal_ids: ["550e8400-e29b-41d4-a716-446655440000"],
    signal_type: "A",
    signal_strength: 0.55,
    affected_entity_types: ["T"],
    ontology_module: "finance",
    example_documents: ["doc-a"],
    example_text_snippets: ["snippet one", "snippet two"],
    evidence_summary_nl: "Summary here",
  },
  signal_type: "signal_a",
  raw_confidence: 0.5,
  priority: "medium",
  status: "pending",
  current_schema_version_id: "550e8400-e29b-41d4-a716-446655440001",
  ontology_module: "finance",
  dedup_hash: "ab",
  overflow: false,
  generated_at: "2026-05-13T00:00:00Z",
};

describe("ProposalsListPage", () => {
  it("renders status filter options matching ProposalStatus (no auto_applied)", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ items: [], next_cursor: null }),
    ) as unknown as typeof fetch;

    render(<ProposalsListPage />);
    const selects = await screen.findAllByRole("combobox");
    const statusSelect = selects[1] as HTMLSelectElement;
    const options = Array.from(statusSelect.querySelectorAll("option")).map(
      (o) => o.value,
    );
    expect(options).toContain("deferred");
    expect(options).toContain("superseded");
    expect(options).not.toContain("auto_applied");
  });

  it("lists proposals returned by the API", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({ items: [sampleProposal], next_cursor: null }),
    ) as unknown as typeof fetch;

    render(<ProposalsListPage />);
    await waitFor(() => {
      expect(screen.getByText("create class X")).toBeTruthy();
    });
  });

  it("uses copy-registry labels for status badges", async () => {
    globalThis.fetch = vi.fn(async () =>
      jsonResponse({
        items: [{ ...sampleProposal, status: "deferred" }],
        next_cursor: null,
      }),
    ) as unknown as typeof fetch;

    render(<ProposalsListPage />);
    await waitFor(() => {
      expect(screen.getByText(PROPOSALS_COPY.statusDeferred)).toBeTruthy();
    });
  });
});

describe("ProposalDetailPage", () => {
  it("renders example snippets and documents from evidence bundle", async () => {
    globalThis.fetch = vi.fn(async (url: string | URL) => {
      const u = typeof url === "string" ? url : url.toString();
      if (u.includes("/proposals/p1") && !u.includes("/decide")) {
        return jsonResponse(sampleProposal);
      }
      return jsonResponse({}, 404);
    }) as unknown as typeof fetch;

    render(<ProposalDetailPage />);
    await waitFor(() => {
      expect(screen.getByTestId("evidence-snippets")).toBeTruthy();
      expect(screen.getByTestId("evidence-documents")).toBeTruthy();
    });
    expect(screen.getByText("snippet one")).toBeTruthy();
    expect(screen.getByText("doc-a")).toBeTruthy();
  });

  it("posts deferred decision to the decide endpoint", async () => {
    // Phase-7 fix: keep the ``vi.Mock`` reference so ``.mock.calls`` is
    // typed; only the ``globalThis.fetch`` assignment needs the
    // ``typeof fetch`` cast. The previous shape cast the mock itself,
    // hiding the ``.mock`` accessor from TypeScript.
    const fetchMock = vi.fn(
      async (url: string | URL, init?: RequestInit) => {
        const u = typeof url === "string" ? url : url.toString();
        if (u.includes("/proposals/p1") && !u.includes("/decide")) {
          return jsonResponse(sampleProposal);
        }
        if (u.includes("/decide")) {
          return jsonResponse({ ...sampleProposal, status: "deferred" });
        }
        return jsonResponse({}, 404);
      },
    );
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const user = userEvent.setup();
    render(<ProposalDetailPage />);
    await waitFor(() =>
      expect(screen.getByTestId("proposal-decision-bar")).toBeTruthy(),
    );
    await user.type(
      screen.getByPlaceholderText(PROPOSALS_COPY.decisionReviewerPlaceholder),
      "rev",
    );
    const deferBtn = screen.getByRole("button", {
      name: PROPOSALS_COPY.decisionDefer,
    });
    await user.click(deferBtn);
    await waitFor(() => {
      const hit = fetchMock.mock.calls.some(
        (c: [url: string | URL, init?: RequestInit]) => {
          const init = c[1];
          if (init?.method !== "POST") return false;
          const b = init.body;
          const s = typeof b === "string" ? b : String(b);
          return s.includes("deferred");
        },
      );
      expect(hit).toBe(true);
    });
  });
});
