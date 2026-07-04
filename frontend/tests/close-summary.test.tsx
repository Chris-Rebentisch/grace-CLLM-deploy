import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SummaryView } from "@/components/session/SummaryView";
import type { SessionSummary } from "@/lib/api/types";

function buildSummary(narrative = "original narrative"): SessionSummary {
  return {
    narrative,
    ontology_changes: [],
    cqs_flipped_state: [],
    decisions_recorded: [],
    deferred_items: [],
    certainty_band_shifts: [],
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("SummaryView (D200)", () => {
  it("inline edit marks summary as edited and Confirm-and-Save commits with edited=true", async () => {
    const onConfirm = vi.fn();
    render(
      <SummaryView
        summary={buildSummary()}
        sessionClosed={false}
        onConfirmSave={onConfirm}
        onReturnToChat={() => {}}
      />,
    );
    const textarea = screen.getByTestId("summary-textarea");
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "a richer narrative");
    expect(screen.getByTestId("summary-unsaved-indicator")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("confirm-save"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    const call = onConfirm.mock.calls[0][0];
    expect(call.edited).toBe(true);
    expect(call.finalSummary.narrative).toBe("a richer narrative");
  });

  it("no-edit Confirm-and-Save commits with edited=false", async () => {
    const onConfirm = vi.fn();
    render(
      <SummaryView
        summary={buildSummary()}
        sessionClosed={false}
        onConfirmSave={onConfirm}
        onReturnToChat={() => {}}
      />,
    );
    await userEvent.click(screen.getByTestId("confirm-save"));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onConfirm.mock.calls[0][0].edited).toBe(false);
  });

  it("Return to Chat requires discard confirmation when edits are present", async () => {
    const onReturn = vi.fn();
    render(
      <SummaryView
        summary={buildSummary()}
        sessionClosed={false}
        onConfirmSave={() => {}}
        onReturnToChat={onReturn}
      />,
    );
    const textarea = screen.getByTestId("summary-textarea");
    await userEvent.type(textarea, " edits");

    const returnBtn = screen.getByTestId("return-to-chat");
    await userEvent.click(returnBtn);
    expect(onReturn).not.toHaveBeenCalled(); // first click arms discard
    expect(returnBtn.textContent).toMatch(/Discard edits/i);

    await userEvent.click(returnBtn);
    expect(onReturn).toHaveBeenCalledTimes(1);
    expect(onReturn.mock.calls[0][0].edited).toBe(true);
  });

  it("D200/D203 beforeunload guard: registers when edited, unregisters when cleared or session closed", async () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");

    const { rerender, unmount } = render(
      <SummaryView
        summary={buildSummary()}
        sessionClosed={false}
        onConfirmSave={() => {}}
        onReturnToChat={() => {}}
      />,
    );
    // No edits → no beforeunload yet.
    expect(addSpy.mock.calls.some(([t]) => t === "beforeunload")).toBe(false);

    await userEvent.type(screen.getByTestId("summary-textarea"), " now edited");
    expect(addSpy.mock.calls.some(([t]) => t === "beforeunload")).toBe(true);

    // Closing the session clears the warning.
    rerender(
      <SummaryView
        summary={{ ...buildSummary(), narrative: "original narrative now edited" }}
        sessionClosed={true}
        onConfirmSave={() => {}}
        onReturnToChat={() => {}}
      />,
    );
    expect(removeSpy.mock.calls.some(([t]) => t === "beforeunload")).toBe(true);

    unmount();
  });
});
