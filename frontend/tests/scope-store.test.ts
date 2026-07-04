import { describe, expect, it, beforeEach } from "vitest";
import { useScopeStore } from "@/lib/state/scope-store";

beforeEach(() => {
  useScopeStore.getState().selectAll();
});

describe("scope-store", () => {
  it("tracks multi-segment selection", () => {
    const store = useScopeStore.getState();
    store.toggleSegment("finance");
    expect(useScopeStore.getState().selectedSegments).toEqual(["finance"]);
    expect(useScopeStore.getState().isAllSegments).toBe(false);

    store.toggleSegment("legal");
    expect(useScopeStore.getState().selectedSegments).toEqual([
      "finance",
      "legal",
    ]);

    // Toggle off
    store.toggleSegment("finance");
    expect(useScopeStore.getState().selectedSegments).toEqual(["legal"]);
  });

  it("clears selection on session end (selectAll)", () => {
    const store = useScopeStore.getState();
    store.setSegments(["finance", "legal"]);
    expect(useScopeStore.getState().isAllSegments).toBe(false);

    store.selectAll();
    expect(useScopeStore.getState().isAllSegments).toBe(true);
    expect(useScopeStore.getState().selectedSegments).toEqual([]);
  });
});
