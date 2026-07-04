import { beforeEach, describe, expect, it } from "vitest";
import { checkMount } from "@/lib/phase/open-guard";
import { useSessionStore } from "@/lib/state/session-store";
import {
  clearRecentTelemetry,
  getRecentTelemetry,
} from "@/lib/telemetry/bus";

beforeEach(() => {
  useSessionStore.getState().clearSession();
  clearRecentTelemetry();
});

describe("EC-4 open-phase guard (D196)", () => {
  it("blocks attention-stealing mounts during Open and emits protocol_violation_detected", () => {
    useSessionStore.getState().startSession("open");

    const cases = [
      "modal",
      "toast",
      "snackbar",
      "alert",
      "browser_prompt",
      "system_redirect",
      "background_refetch_jump",
      "focus_steal",
      "layout_shift_mount",
    ] as const;

    for (const mountType of cases) {
      const result = checkMount(mountType, { source: "test" });
      expect(result.allowed).toBe(false);
      expect(result.reason).toBe("open_phase_forbidden");
    }

    const events = getRecentTelemetry().filter(
      (e) => e.type === "protocol_violation_detected",
    );
    expect(events).toHaveLength(cases.length);
    for (const e of events) {
      expect((e.payload as { violation_type: string }).violation_type).toBe(
        "ec4_attention_stealing_mount",
      );
    }
  });

  it("allows positive-list passive renders without emitting a violation (D196 positive list)", () => {
    useSessionStore.getState().startSession("open");
    const allowed = [
      "response_paint",
      "a11y_announcement",
      "react_reconciliation_remount",
      "latency_milestone_update",
      "scroll_anchor",
    ] as const;
    for (const mt of allowed) {
      const result = checkMount(mt);
      expect(result.allowed).toBe(true);
      expect(result.reason).toBe("positive_list");
    }
    const violations = getRecentTelemetry().filter(
      (e) => e.type === "protocol_violation_detected",
    );
    expect(violations).toHaveLength(0);
  });

  it("guard deactivates when the Open phase exits — forbidden mounts are then allowed silently", () => {
    useSessionStore.getState().startSession("open");
    expect(checkMount("toast").allowed).toBe(false);

    useSessionStore.getState().enterPhase("close");
    clearRecentTelemetry();

    const result = checkMount("toast");
    expect(result.allowed).toBe(true);
    expect(result.reason).toBe("not_open_phase");
    expect(
      getRecentTelemetry().filter(
        (e) => e.type === "protocol_violation_detected",
      ),
    ).toHaveLength(0);
  });
});
