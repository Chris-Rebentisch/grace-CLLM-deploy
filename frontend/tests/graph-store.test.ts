import { beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "@/lib/state/graph-store";

beforeEach(() => {
  useGraphStore.getState().reset();
});

describe("graph-store", () => {
  it("setLayout toggles between fcose and dagre", () => {
    expect(useGraphStore.getState().activeLayout).toBe("fcose");
    useGraphStore.getState().setLayout("dagre");
    expect(useGraphStore.getState().activeLayout).toBe("dagre");
    useGraphStore.getState().setLayout("fcose");
    expect(useGraphStore.getState().activeLayout).toBe("fcose");
  });

  it("selection is exclusive — node and edge cannot both be set", () => {
    useGraphStore.getState().selectNode("n1");
    expect(useGraphStore.getState().selectedNodeId).toBe("n1");
    expect(useGraphStore.getState().selectedEdgeId).toBeNull();

    useGraphStore.getState().selectEdge("e1");
    expect(useGraphStore.getState().selectedEdgeId).toBe("e1");
    expect(useGraphStore.getState().selectedNodeId).toBeNull();

    useGraphStore.getState().clearSelection();
    expect(useGraphStore.getState().selectedNodeId).toBeNull();
    expect(useGraphStore.getState().selectedEdgeId).toBeNull();
  });

  it("pagination cursor advances and resets", () => {
    expect(useGraphStore.getState().paginationCursor).toBeNull();
    useGraphStore.getState().setCursor("cursor-abc");
    expect(useGraphStore.getState().paginationCursor).toBe("cursor-abc");
    useGraphStore.getState().resetCursor();
    expect(useGraphStore.getState().paginationCursor).toBeNull();
  });

  it("toggleEntityType/relationshipType is idempotent per-type", () => {
    useGraphStore.getState().toggleEntityType("Legal_Entity");
    expect(useGraphStore.getState().visibleEntityTypes.has("Legal_Entity")).toBe(
      true,
    );
    useGraphStore.getState().toggleEntityType("Legal_Entity");
    expect(useGraphStore.getState().visibleEntityTypes.has("Legal_Entity")).toBe(
      false,
    );

    useGraphStore.getState().toggleRelationshipType("owns");
    expect(
      useGraphStore.getState().visibleRelationshipTypes.has("owns"),
    ).toBe(true);
  });
});
