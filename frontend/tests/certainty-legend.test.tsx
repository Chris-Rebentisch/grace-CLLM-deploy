import { beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CertaintyLegend } from "@/components/provenance/CertaintyLegend";
import { useSessionStore } from "@/lib/state/session-store";

beforeEach(() => {
  useSessionStore.getState().clearSession();
});

describe("CertaintyLegend", () => {
  it("D199: renders once when a session is active; hidden when idle", () => {
    const { rerender } = render(<CertaintyLegend />);
    expect(screen.queryByTestId("certainty-legend")).toBeNull();

    useSessionStore.getState().startSession("open");
    rerender(<CertaintyLegend />);
    const legend = screen.getByTestId("certainty-legend");
    expect(legend).toBeInTheDocument();
    // Expanded by default on first Open entry.
    expect(legend.dataset.collapsed).toBe("false");
    expect(screen.getByTestId("legend-list")).toBeInTheDocument();
  });

  it("toggles collapse state via session-store and persists within the session", async () => {
    useSessionStore.getState().startSession("open");
    render(<CertaintyLegend />);
    const toggle = screen.getByTestId("legend-toggle");
    await userEvent.click(toggle);

    expect(useSessionStore.getState().legendCollapsedThisSession).toBe(true);
    expect(screen.queryByTestId("legend-list")).toBeNull();
    expect(screen.getByTestId("certainty-legend").dataset.collapsed).toBe(
      "true",
    );
  });
});
