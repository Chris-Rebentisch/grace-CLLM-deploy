// Chunk 28 D214 — layout config map behind a library-agnostic selector.
// Consumers pass `"fcose" | "dagre"` to GraphCanvas; GraphCanvas reads
// from this module. No other file imports Cytoscape-specific names.

export const FCOSE_LAYOUT = {
  name: "fcose",
  quality: "default",
  animate: true,
  randomize: false,
  nodeRepulsion: 4500,
  idealEdgeLength: 100,
  nodeSeparation: 75,
  fit: true,
  padding: 30,
} as const;

export const DAGRE_LAYOUT = {
  name: "dagre",
  rankDir: "TB",
  nodeSep: 50,
  edgeSep: 10,
  rankSep: 75,
  fit: true,
  padding: 30,
  animate: true,
} as const;

export type LayoutName = "fcose" | "dagre";

export function getLayoutConfig(name: LayoutName) {
  return name === "fcose" ? FCOSE_LAYOUT : DAGRE_LAYOUT;
}
