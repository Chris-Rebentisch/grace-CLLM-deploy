import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ChangeDirectiveAuthoringForm } from "@/components/change-directives/ChangeDirectiveAuthoringForm";

const originalFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockFetch(captured: { url: string; init: RequestInit }[]) {
  return (async (url: string, init: RequestInit) => {
    captured.push({ url, init });
    return new Response(
      JSON.stringify({ directive_id: "dir-123" }),
      { status: 201, headers: { "Content-Type": "application/json" } },
    );
  }) as unknown as typeof fetch;
}

describe("ChangeDirectiveAuthoringForm", () => {
  it("submits an Operational_Adjustment with default tier", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    globalThis.fetch = mockFetch(calls);
    const onCreated = vi.fn();
    render(
      <ChangeDirectiveAuthoringForm
        sessionId="sess-1"
        flaggedFromElementName="Legal_Entity"
        onCreated={onCreated}
      />,
    );
    fireEvent.change(screen.getByTestId("cd-title"), {
      target: { value: "Reorg client tiering" },
    });
    fireEvent.change(screen.getByTestId("cd-description"), {
      target: { value: "We are restructuring the client tier hierarchy." },
    });
    fireEvent.click(screen.getByTestId("cd-submit"));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("dir-123"));
    expect(calls).toHaveLength(1);
    const sent = JSON.parse(calls[0].init.body as string);
    expect(sent.tier).toBe("Operational_Adjustment");
    expect(sent.flagged_from_session_id).toBe("sess-1");
    expect(sent.flagged_from_element_name).toBe("Legal_Entity");
  });

  it("submits a Strategic_Initiative when tier toggle is set", async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    globalThis.fetch = mockFetch(calls);
    const onCreated = vi.fn();
    render(
      <ChangeDirectiveAuthoringForm
        sessionId="sess-2"
        flaggedFromElementName="Insurance_Policy"
        onCreated={onCreated}
      />,
    );
    fireEvent.click(screen.getByTestId("tier-si"));
    expect(screen.getByTestId("cd-strategic-fields")).toBeTruthy();
    fireEvent.change(screen.getByTestId("cd-title"), {
      target: { value: "Move to consolidated underwriting" },
    });
    fireEvent.change(screen.getByTestId("cd-description"), {
      target: { value: "Merge two underwriting tracks." },
    });
    fireEvent.change(screen.getByTestId("cd-target-state"), {
      target: { value: "Single underwriting pipeline." },
    });
    fireEvent.change(screen.getByTestId("cd-initial-criterion"), {
      target: { value: "All policies share one underwriter pool." },
    });
    fireEvent.click(screen.getByTestId("cd-submit"));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("dir-123"));
    const sent = JSON.parse(calls[0].init.body as string);
    expect(sent.tier).toBe("Strategic_Initiative");
    expect(sent.target_state_description).toBe("Single underwriting pipeline.");
    expect(sent.initial_evidence_criteria).toEqual([
      "All policies share one underwriter pool.",
    ]);
  });
});
