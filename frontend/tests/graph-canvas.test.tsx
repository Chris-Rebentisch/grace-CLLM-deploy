import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import type {
  GraphCanvasProps,
  GraphEdgeData,
  GraphNodeData,
} from "@/components/graph/GraphCanvas";

// Cytoscape renders to a WebGL/Canvas element that jsdom does not
// implement. Stub `react-cytoscapejs` with a DOM-only placeholder so the
// wrapper's props wiring + strict-mode cleanup are what the test actually
// exercises. This is the standard jsdom pattern for Canvas-rendering libs.
vi.mock("react-cytoscapejs", () => ({
  default: (props: Record<string, unknown>) => {
    return (
      <div
        data-testid="cytoscape-stub"
        data-elements-count={
          Array.isArray(props.elements)
            ? (props.elements as unknown[]).length
            : 0
        }
        data-layout-name={
          typeof props.layout === "object" && props.layout !== null
            ? (props.layout as { name?: string }).name
            : ""
        }
      />
    );
  },
}));

// Stub Cytoscape core + extensions: the wrapper calls `cytoscape.use(...)`
// at module top. Only `use` is exercised under jsdom; real runtime gets
// the real package.
vi.mock("cytoscape", () => ({
  default: { use: vi.fn() },
}));
vi.mock("cytoscape-fcose", () => ({ default: {} }));
vi.mock("cytoscape-dagre", () => ({ default: {} }));

import { GraphCanvas } from "@/components/graph/GraphCanvas";

const NODES: GraphNodeData[] = [
  {
    id: "n1",
    label: "Acme",
    entityType: "Legal_Entity",
    ontologyModule: "legal_entity",
  },
  {
    id: "n2",
    label: "Acme Contract",
    entityType: "Contract",
    ontologyModule: "contract",
  },
  {
    id: "n3",
    label: "Main St Property",
    entityType: "Property",
    ontologyModule: "real_estate",
  },
];

const EDGES: GraphEdgeData[] = [
  { id: "e1", source: "n1", target: "n2", label: "signed" },
  { id: "e2", source: "n2", target: "n3", label: "covers" },
];

function renderCanvas(overrides: Partial<GraphCanvasProps> = {}) {
  return render(
    <GraphCanvas
      nodes={NODES}
      edges={EDGES}
      layout="fcose"
      {...overrides}
    />,
  );
}

describe("GraphCanvas (CP1 wrapper)", () => {
  it("renders a 3-node / 2-edge fixture through the library-agnostic interface", () => {
    const { getByTestId } = renderCanvas();
    const root = getByTestId("graph-canvas-root");
    expect(root).toBeTruthy();
    const stub = getByTestId("cytoscape-stub");
    // 3 nodes + 2 edges = 5 elements delivered to the underlying lib
    expect(stub.dataset.elementsCount).toBe("5");
    expect(stub.dataset.layoutName).toBe("fcose");
  });

  it("unmounts cleanly (R3: strict-mode safe — no duplicate instances leaked)", () => {
    const warnings: string[] = [];
    const spy = vi
      .spyOn(console, "error")
      .mockImplementation((msg: unknown) => {
        warnings.push(String(msg));
      });
    const { unmount } = renderCanvas({ layout: "dagre" });
    unmount();
    // Nothing should have been logged to console.error on mount or unmount;
    // in particular no React strict-mode memory-leak warnings.
    expect(warnings.filter((w) => /leak|unmount/i.test(w))).toEqual([]);
    spy.mockRestore();
  });
});
