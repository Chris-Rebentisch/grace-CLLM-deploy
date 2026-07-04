import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { RestorePriorStateDialog } from "@/components/autonomy/RestorePriorStateDialog";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";

describe("RestorePriorStateDialog", () => {
  const previousState = {
    "Tier 1": true,
    "Tier 2": false,
    "Tier 3": false,
  };

  it("renders per-tier state preview", () => {
    render(
      <RestorePriorStateDialog
        previousState={previousState}
        loading={false}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );

    expect(screen.getByTestId("restore-state-dialog")).toBeInTheDocument();
    expect(screen.getByText(AUTONOMY_COPY.restoreStateHeading)).toBeInTheDocument();

    // Tier 1 was enabled before engage — will be re-enabled
    const tier1 = screen.getByTestId("restore-tier-Tier 1");
    expect(tier1.textContent).toContain(AUTONOMY_COPY.restoreStateTierEnabled);

    // Tier 2 was disabled before engage — will remain disabled
    const tier2 = screen.getByTestId("restore-tier-Tier 2");
    expect(tier2.textContent).toContain(AUTONOMY_COPY.restoreStateTierDisabled);

    // Tier 3 was disabled before engage — will remain disabled
    const tier3 = screen.getByTestId("restore-tier-Tier 3");
    expect(tier3.textContent).toContain(AUTONOMY_COPY.restoreStateTierDisabled);
  });

  it("confirm button fires onConfirm callback", () => {
    const onConfirm = vi.fn();
    render(
      <RestorePriorStateDialog
        previousState={previousState}
        loading={false}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("restore-state-confirm"));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it("cancel button fires onCancel callback", () => {
    const onCancel = vi.fn();
    render(
      <RestorePriorStateDialog
        previousState={previousState}
        loading={false}
        onConfirm={() => {}}
        onCancel={onCancel}
      />,
    );

    fireEvent.click(screen.getByTestId("restore-state-cancel"));
    expect(onCancel).toHaveBeenCalledOnce();
  });
});
