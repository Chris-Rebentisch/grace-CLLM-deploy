"use client";

// Chunk 28 D210 / D214 — library wrapper.
// Isolates Cytoscape.js behind a library-agnostic prop interface. No other
// frontend file should import `cytoscape`, `react-cytoscapejs`,
// `cytoscape-fcose`, or `cytoscape-dagre` directly.
//
// React-strict-mode safety (R3): the Cytoscape instance is held in a ref
// and destroyed in the useEffect cleanup. Single mount lifecycle; no
// duplicate instances under strict-mode double-invocation.

import { useEffect, useRef } from "react";
import CytoscapeComponent from "react-cytoscapejs";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import dagre from "cytoscape-dagre";
import { getLayoutConfig, type LayoutName } from "@/lib/graph/layout-adapters";
import {
  getColorForModule,
  getShapeForType,
} from "@/lib/graph/node-shape-map";

// Register extensions once at module load. Cytoscape guards against
// duplicate registration internally, so strict-mode re-imports are safe.
cytoscape.use(fcose);
cytoscape.use(dagre);

export type GraphNodeData = {
  id: string;
  label: string;
  entityType: string;
  ontologyModule: string | null;
};

export type GraphEdgeData = {
  id: string;
  source: string;
  target: string;
  label: string;
};

export type GraphCanvasProps = {
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
  layout: LayoutName;
  highlightedNodeIds?: ReadonlyArray<string>;
  onNodeClick?: (nodeId: string) => void;
  onEdgeClick?: (edgeId: string) => void;
  style?: React.CSSProperties;
};

function toCytoscapeElements(
  nodes: GraphNodeData[],
  edges: GraphEdgeData[],
  highlighted: ReadonlySet<string>,
): ElementDefinition[] {
  const nodeElements: ElementDefinition[] = nodes.map((n) => ({
    group: "nodes",
    data: {
      id: n.id,
      label: n.label,
      shape: getShapeForType(n.entityType),
      color: getColorForModule(n.ontologyModule),
      highlighted: highlighted.has(n.id) ? 1 : 0,
    },
  }));
  const edgeElements: ElementDefinition[] = edges.map((e) => ({
    group: "edges",
    data: {
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.label,
    },
  }));
  return [...nodeElements, ...edgeElements];
}

const CY_STYLESHEET = [
  {
    selector: "node",
    style: {
      "background-color": "data(color)",
      shape: "data(shape)",
      label: "data(label)",
      color: "#0f172a",
      "font-size": 11,
      "text-valign": "center",
      "text-halign": "center",
      "text-max-width": 120,
      "text-wrap": "wrap",
      width: 56,
      height: 40,
    },
  },
  {
    selector: "node[?highlighted]",
    style: {
      "border-width": 3,
      "border-color": "#f59e0b",
    },
  },
  {
    selector: "edge",
    style: {
      "curve-style": "bezier",
      "target-arrow-shape": "triangle",
      "line-color": "#94a3b8",
      "target-arrow-color": "#94a3b8",
      width: 1.5,
      label: "data(label)",
      "font-size": 9,
      color: "#475569",
      "text-background-color": "#ffffff",
      "text-background-opacity": 0.8,
      "text-background-padding": 2,
    },
  },
];

export function GraphCanvas(props: GraphCanvasProps) {
  const { nodes, edges, layout, highlightedNodeIds, onNodeClick, onEdgeClick } =
    props;
  const cyRef = useRef<Core | null>(null);
  const highlightedSet = new Set(highlightedNodeIds ?? []);
  const elements = toCytoscapeElements(nodes, edges, highlightedSet);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    const nodeHandler = (evt: cytoscape.EventObject) => {
      onNodeClick?.(evt.target.id());
    };
    const edgeHandler = (evt: cytoscape.EventObject) => {
      onEdgeClick?.(evt.target.id());
    };
    cy.on("tap", "node", nodeHandler);
    cy.on("tap", "edge", edgeHandler);
    return () => {
      cy.off("tap", "node", nodeHandler);
      cy.off("tap", "edge", edgeHandler);
    };
  }, [onNodeClick, onEdgeClick]);

  useEffect(() => {
    // Strict-mode cleanup: destroy the Cytoscape instance when the
    // wrapper unmounts so duplicate instances can't leak.
    return () => {
      const cy = cyRef.current;
      if (cy) {
        cy.destroy();
        cyRef.current = null;
      }
    };
  }, []);

  return (
    <div
      data-testid="graph-canvas-root"
      className="w-full h-full min-h-[400px]"
      style={props.style}
    >
      <CytoscapeComponent
        elements={elements}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        stylesheet={CY_STYLESHEET as any}
        layout={getLayoutConfig(layout)}
        style={{ width: "100%", height: "100%" }}
        cy={(cy) => {
          cyRef.current = cy;
        }}
      />
    </div>
  );
}
