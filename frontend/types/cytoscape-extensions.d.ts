// Chunk 28 — module declarations for Cytoscape layout extensions that
// don't ship their own TypeScript types. The extensions expose a
// registration-style default export: `cytoscape.use(fcose)`.

declare module "cytoscape-fcose" {
  import type { Ext } from "cytoscape";
  const fcose: Ext;
  export default fcose;
}

declare module "cytoscape-dagre" {
  import type { Ext } from "cytoscape";
  const dagre: Ext;
  export default dagre;
}
