import { describe, expect, it, beforeEach } from "vitest";
import { useReviewStore } from "@/lib/state/review-store";

beforeEach(() => { useReviewStore.setState({ sessionId: null, currentDecision: null, hoverElement: null, hoverDecision: null, instrumentModalOpen: false, activeInstrument: null }); });

describe("review-store", () => {
  it("tracks session state", () => {
    useReviewStore.getState().setSessionId("s1");
    expect(useReviewStore.getState().sessionId).toBe("s1");
  });

  it("tracks instrument modal state", () => {
    useReviewStore.getState().openInstrument("laddering");
    expect(useReviewStore.getState().instrumentModalOpen).toBe(true);
    expect(useReviewStore.getState().activeInstrument).toBe("laddering");
    useReviewStore.getState().closeInstrument();
    expect(useReviewStore.getState().instrumentModalOpen).toBe(false);
  });
});
