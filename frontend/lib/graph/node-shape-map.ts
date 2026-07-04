// Chunk 28 D210 — entity_type → shape and ontology_module → color mapping.
// v1 ships a hardcoded fallback map. Spec §18 #3 allows future fetch from
// GET /api/ontology/active; that is deferred.

const DEFAULT_SHAPE = "ellipse";
const DEFAULT_COLOR = "#64748b"; // slate-500

const SHAPE_BY_TYPE: Record<string, string> = {
  Legal_Entity: "rectangle",
  Contract: "hexagon",
  Property: "roundrectangle",
  Person: "ellipse",
  Event: "diamond",
  Document: "round-tag",
};

const COLOR_BY_MODULE: Record<string, string> = {
  legal_entity: "#4f46e5", // indigo-600
  real_estate: "#059669", // emerald-600
  contract: "#d97706", // amber-600
  person: "#db2777", // pink-600
  event: "#0891b2", // cyan-600
};

export function getShapeForType(entityType: string): string {
  return SHAPE_BY_TYPE[entityType] ?? DEFAULT_SHAPE;
}

export function getColorForModule(moduleName: string | null): string {
  if (moduleName == null) return DEFAULT_COLOR;
  return COLOR_BY_MODULE[moduleName] ?? DEFAULT_COLOR;
}

// Exposed for tests and legend rendering.
export const DEFAULT_SHAPE_MAP = { ...SHAPE_BY_TYPE } as const;
export const DEFAULT_COLOR_MAP = { ...COLOR_BY_MODULE } as const;
