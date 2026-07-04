import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { KillSwitchButton } from "@/components/autonomy/KillSwitchButton";
import { AUTONOMY_COPY } from "@/lib/autonomy/copy";
import { clearRecentTelemetry, getRecentTelemetry } from "@/lib/telemetry/bus";

vi.mock("@/lib/api/client", () => ({
  apiRequest: vi.fn(),
  getApiBaseUrl: () => "http://127.0.0.1:8000",
}));

describe("KillSwitchButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    clearRecentTelemetry();
  });

  it("renders active status when not engaged", () => {
    render(<KillSwitchButton engaged={false} onToggled={() => {}} />);
    expect(screen.getByTestId("kill-switch-status").textContent).toBe(
      AUTONOMY_COPY.killSwitchStatusActive,
    );
    expect(screen.getByTestId("kill-switch-engage-btn")).toBeInTheDocument();
  });

  it("engage is single-click with no confirmation dialog", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      autonomy_enabled: false,
      tiers_updated: 3,
    });
    const onToggled = vi.fn();

    render(<KillSwitchButton engaged={false} onToggled={onToggled} />);

    // No confirmation dialog should be visible
    expect(
      screen.queryByTestId("kill-switch-confirm-dialog"),
    ).not.toBeInTheDocument();

    // Reason is required — fill it in before clicking engage
    fireEvent.change(screen.getByTestId("kill-switch-reason"), {
      target: { value: "testing engage" },
    });
    fireEvent.click(screen.getByTestId("kill-switch-engage-btn"));

    await waitFor(() => {
      expect(onToggled).toHaveBeenCalledWith(false);
    });

    expect(apiRequest).toHaveBeenCalledWith(
      "/api/ontology/daemon/kill-switch",
      { method: "PATCH", body: { autonomy_enabled: false, reason: "testing engage" } },
    );
  });

  it("engage button is disabled when reason is empty", () => {
    render(<KillSwitchButton engaged={false} onToggled={() => {}} />);
    const btn = screen.getByTestId("kill-switch-engage-btn");
    expect(btn).toBeDisabled();

    fireEvent.change(screen.getByTestId("kill-switch-reason"), {
      target: { value: "a reason" },
    });
    expect(btn).not.toBeDisabled();
  });

  it("disengage shows confirmation dialog before action", async () => {
    render(<KillSwitchButton engaged={true} onToggled={() => {}} />);
    expect(screen.getByTestId("kill-switch-status").textContent).toBe(
      AUTONOMY_COPY.killSwitchStatusStopped,
    );

    // Click disengage button — should show confirmation, not fire API.
    fireEvent.click(screen.getByTestId("kill-switch-disengage-btn"));
    expect(
      screen.getByTestId("kill-switch-confirm-dialog"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(AUTONOMY_COPY.killSwitchDisengageConfirm),
    ).toBeInTheDocument();
  });

  it("disengage confirmation cancel hides dialog", () => {
    render(<KillSwitchButton engaged={true} onToggled={() => {}} />);
    fireEvent.click(screen.getByTestId("kill-switch-disengage-btn"));
    expect(
      screen.getByTestId("kill-switch-confirm-dialog"),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("kill-switch-confirm-cancel"));
    expect(
      screen.queryByTestId("kill-switch-confirm-dialog"),
    ).not.toBeInTheDocument();
  });

  it("emits kill_switch_engaged telemetry on engage", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      autonomy_enabled: false,
      tiers_updated: 3,
    });

    render(<KillSwitchButton engaged={false} onToggled={() => {}} />);
    fireEvent.change(screen.getByTestId("kill-switch-reason"), {
      target: { value: "telemetry test" },
    });
    fireEvent.click(screen.getByTestId("kill-switch-engage-btn"));

    await waitFor(() => {
      const events = getRecentTelemetry();
      const ev = events.find((e) => e.type === "kill_switch_engaged");
      expect(ev).toBeDefined();
    });
  });

  it("emits kill_switch_disengaged telemetry on confirmed disengage", async () => {
    const { apiRequest } = await import("@/lib/api/client");
    (apiRequest as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      autonomy_enabled: true,
      tiers_updated: 3,
    });

    render(<KillSwitchButton engaged={true} onToggled={() => {}} />);
    fireEvent.click(screen.getByTestId("kill-switch-disengage-btn"));
    fireEvent.click(screen.getByTestId("kill-switch-confirm-resume"));

    await waitFor(() => {
      const events = getRecentTelemetry();
      const ev = events.find((e) => e.type === "kill_switch_disengaged");
      expect(ev).toBeDefined();
    });
  });
});
