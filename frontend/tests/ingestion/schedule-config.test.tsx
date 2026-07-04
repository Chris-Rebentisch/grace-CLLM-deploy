import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ScheduleConfigFields } from "@/components/ingestion/ScheduleConfigFields";

describe("ScheduleConfigFields", () => {
  const defaultProps = {
    scheduleEnabled: false,
    scheduleMode: "interval",
    scheduleIntervalHours: 1,
    onScheduleEnabledChange: vi.fn(),
    onScheduleModeChange: vi.fn(),
    onScheduleIntervalChange: vi.fn(),
  };

  it("renders heading and enable checkbox", () => {
    render(<ScheduleConfigFields {...defaultProps} />);
    expect(screen.getByText(/Ingestion schedule/i)).toBeTruthy();
    expect(screen.getByText(/Enable scheduled ingestion/i)).toBeTruthy();
  });

  it("shows mode selector when enabled", () => {
    render(
      <ScheduleConfigFields {...defaultProps} scheduleEnabled={true} />,
    );
    expect(screen.getByText(/Schedule mode/i)).toBeTruthy();
    expect(screen.getByText(/Recurring interval/i)).toBeTruthy();
    expect(screen.getByText(/One-time run/i)).toBeTruthy();
  });

  it("calls onScheduleEnabledChange when checkbox toggled", () => {
    const handler = vi.fn();
    render(
      <ScheduleConfigFields
        {...defaultProps}
        onScheduleEnabledChange={handler}
      />,
    );
    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(handler).toHaveBeenCalledWith(true);
  });
});
