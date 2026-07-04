import { beforeEach, describe, expect, it } from "vitest";
import { useSessionStore } from "@/lib/state/session-store";

beforeEach(() => {
  useSessionStore.getState().clearSession();
});

describe("useSessionStore", () => {
  it("startSession generates a UUID4 id, sets active status, seeds phase history", () => {
    const id = useSessionStore.getState().startSession("open");
    expect(id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
    );
    const state = useSessionStore.getState();
    expect(state.sessionId).toBe(id);
    expect(state.sessionStatus).toBe("active");
    expect(state.activePhase).toBe("open");
    expect(state.phaseHistory).toHaveLength(1);
    expect(state.phaseHistory[0].phase).toBe("open");
    expect(state.phaseHistory[0].exited_at).toBeNull();
  });

  it("enterPhase closes the prior history entry and opens the next", () => {
    useSessionStore.getState().startSession("open");
    useSessionStore.getState().enterPhase("close");
    const history = useSessionStore.getState().phaseHistory;
    expect(history).toHaveLength(2);
    expect(history[0].exited_at).not.toBeNull();
    expect(history[0].duration_ms).toBeGreaterThanOrEqual(0);
    expect(history[1].phase).toBe("close");
    expect(history[1].exited_at).toBeNull();
  });

  it("pause → resume does not emit cooldown/penalty/decay fields (EC-5)", async () => {
    useSessionStore.getState().startSession("open");
    useSessionStore.getState().pauseSession();
    expect(useSessionStore.getState().sessionStatus).toBe("paused");

    useSessionStore.getState().resumeSession();
    // Flush the queueMicrotask inside resumeSession.
    await Promise.resolve();
    const state = useSessionStore.getState();

    expect(["active", "resumed"]).toContain(state.sessionStatus);
    expect(state.pausedAt).toBeNull();
    expect(state.resumedFrom).toBe("open");
    // EC-5 audit: assert the state shape has no forbidden fields.
    const forbidden = ["cooldown", "penalty", "decay", "streak"];
    for (const key of forbidden) {
      expect(Object.prototype.hasOwnProperty.call(state, key)).toBe(false);
    }
  });

  it("closeSession transitions status to closed; clearSession resets all state", () => {
    useSessionStore.getState().startSession("open");
    useSessionStore.getState().closeSession();
    expect(useSessionStore.getState().sessionStatus).toBe("closed");

    useSessionStore.getState().clearSession();
    const s = useSessionStore.getState();
    expect(s.sessionId).toBeNull();
    expect(s.sessionStatus).toBe("idle");
    expect(s.phaseHistory).toEqual([]);
  });
});
