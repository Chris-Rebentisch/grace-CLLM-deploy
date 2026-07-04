// Tests for `frontend/components/recon/ScheduleEditor.tsx` (Chunk 37, D287 / D288).

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ScheduleEditor } from "@/components/recon/ScheduleEditor";

describe("ScheduleEditor (Chunk 37, D287 / D288)", () => {
  it("submits the selected cadence and enabled flag", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <ScheduleEditor
        open
        initialCadence="monthly"
        initialEnabled
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    const quarterly = screen
      .getByTestId("schedule-editor-cadence-quarterly")
      .querySelector('input[type="radio"]') as HTMLInputElement;
    fireEvent.click(quarterly);
    fireEvent.click(screen.getByTestId("schedule-editor-submit"));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    expect(onSubmit.mock.calls[0]![0]).toEqual({
      cadence: "quarterly",
      enabled: true,
    });
  });

  it("supports the on-demand path and disabled flag", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(
      <ScheduleEditor
        open
        initialCadence="monthly"
        initialEnabled
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    const onDemand = screen
      .getByTestId("schedule-editor-cadence-on_demand")
      .querySelector('input[type="radio"]') as HTMLInputElement;
    fireEvent.click(onDemand);
    fireEvent.click(screen.getByTestId("schedule-editor-enabled"));
    fireEvent.click(screen.getByTestId("schedule-editor-submit"));
    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledTimes(1);
    });
    expect(onSubmit.mock.calls[0]![0]).toEqual({
      cadence: "on_demand",
      enabled: false,
    });
  });
});
