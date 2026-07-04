import { describe, expect, it, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AirgapToggle } from "@/components/settings/AirgapToggle";
import { useSettingsStore } from "@/lib/state/settings-store";
import { onTelemetry, clearRecentTelemetry } from "@/lib/telemetry/bus";

beforeEach(() => {
  clearRecentTelemetry();
  useSettingsStore.setState({
    draft: {
      provider: "ollama",
      model: "qwen2.5:7b",
      base_url: "http://localhost:11434",
      timeout: 60,
      api_key: "",
      airgap_mode: true,
    },
    baseSnapshot: null,
    airgapBreakDialogOpen: false,
  });
});

describe("AirgapToggle", () => {
  it("flips airgap_mode and emits airgap_mode_toggled with the new value", () => {
    const seen: { type: string; payload?: Record<string, unknown> }[] = [];
    const unsub = onTelemetry((e) => seen.push({ type: e.type, payload: e.payload }));
    render(<AirgapToggle />);
    const checkbox = screen.getByTestId("airgap-toggle-input") as HTMLInputElement;
    expect(checkbox.checked).toBe(true);
    fireEvent.click(checkbox);
    expect(useSettingsStore.getState().draft?.airgap_mode).toBe(false);
    const evt = seen.find((e) => e.type === "airgap_mode_toggled");
    expect(evt).toBeTruthy();
    expect(evt?.payload?.enabled).toBe(false);
    unsub();
  });
});
