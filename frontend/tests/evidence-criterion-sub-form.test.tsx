import { afterEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { EvidenceCriterionSubForm } from "@/components/change-directives/EvidenceCriterionSubForm";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

type Capture = { url: string; init: RequestInit };

function makeFetch(
  captured: Capture[],
  responder: (url: string, init: RequestInit) => unknown,
) {
  return (async (url: string, init: RequestInit) => {
    captured.push({ url, init });
    return new Response(JSON.stringify(responder(url, init)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  }) as unknown as typeof fetch;
}

describe("EvidenceCriterionSubForm", () => {
  it("compiles NL → proposed query, then approve / edit / manual override", async () => {
    const calls: Capture[] = [];
    let phase: "compile" | "patch" = "compile";
    globalThis.fetch = makeFetch(calls, (_url, init) => {
      if (phase === "compile") {
        phase = "patch";
        return {
          criterion_id: "c-1",
          directive_id: "d-1",
          natural_language: "all policies merged",
          measurement_kind: null,
          target_value: null,
          target_satisfied_when: null,
          compiled_query: "MATCH (p:Policy) RETURN count(p)",
          compilation_status: "proposed",
          error_detail: null,
          created_at: "2026-05-07T00:00:00Z",
          updated_at: "2026-05-07T00:00:00Z",
        };
      }
      const body = JSON.parse(init.body as string);
      return {
        criterion_id: "c-1",
        directive_id: "d-1",
        natural_language: "all policies merged",
        measurement_kind: null,
        target_value: null,
        target_satisfied_when: null,
        compiled_query: body.compiled_query ?? "MATCH (p:Policy) RETURN count(p)",
        compilation_status:
          body.action === "approve"
            ? "approved"
            : body.action === "manual_override"
              ? "manually_authored"
              : "proposed",
        error_detail: null,
        created_at: "2026-05-07T00:00:00Z",
        updated_at: "2026-05-07T00:00:00Z",
      };
    });
    render(<EvidenceCriterionSubForm directiveId="d-1" />);
    fireEvent.change(
      screen.getByLabelText(/Evidence criterion/i),
      { target: { value: "all policies merged" } },
    );
    fireEvent.click(screen.getByText(/Compile to query/i));
    await waitFor(() => screen.getByTestId("criterion-review"));
    expect(screen.getByTestId("proposed-query").textContent).toContain(
      "MATCH (p:Policy)",
    );
    fireEvent.click(screen.getByTestId("criterion-approve"));
    await waitFor(() =>
      expect(calls.some((c) => c.url.includes("/criteria/c-1"))).toBe(true),
    );
    const approveCall = calls.find((c) => c.url.includes("/criteria/c-1"))!;
    expect(JSON.parse(approveCall.init.body as string).action).toBe(
      "approve",
    );

    // Manual override path uses the editor body.
    fireEvent.change(screen.getByTestId("criterion-query-editor"), {
      target: { value: "MATCH (n) RETURN 1" },
    });
    fireEvent.click(screen.getByTestId("criterion-manual"));
    await waitFor(() =>
      expect(
        calls.filter((c) => c.url.includes("/criteria/c-1")).length,
      ).toBeGreaterThanOrEqual(2),
    );
  });
});
