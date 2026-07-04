// Chunk 36 D280 / D283 — TypeScript mirror of src/api/recon_models.py.
// Field names must match the Pydantic models verbatim. The fourth
// `recon-api` target in scripts/check-api-contract.sh diffs this file
// against the Python module.
//
// User-facing surfaces only — never use the internal `erd_*` vocabulary
// in this file (EC-8 forbidden-vocabulary scan).

import type { CoveringDirective } from "./types";

export type EmphasizedWithEvidenceItem = {
  element_name: string;
  element_type: string;
  instance_count: number;
  top_evidence_extraction_event_ids: string[];
};

export type EmphasizedWithoutEvidenceItem = {
  element_name: string;
  element_type: string;
  instance_count: number;
  suggested_actions: string[];
};

export type UnemphasizedInEvidenceItem = {
  element_name: string;
  element_type: string;
  instance_count: number;
  decision_status: string;
};

export type GapReportSection =
  | EmphasizedWithEvidenceItem
  | EmphasizedWithoutEvidenceItem
  | UnemphasizedInEvidenceItem;

export type GapReportResponse = {
  session_id: string;
  reviewer: string;
  generated_at: string;
  evidence_grounding_score: number | null;
  evidence_grounding_threshold: number;
  graph_population_floor_breach: string | null;
  emphasized_with_evidence: EmphasizedWithEvidenceItem[];
  emphasized_without_evidence: EmphasizedWithoutEvidenceItem[];
  unemphasized_in_evidence: UnemphasizedInEvidenceItem[];
  // Chunk 60, CP8 — source-type breakdown for filter chips.
  source_type_breakdown?: Record<string, number>;
  // D297 — Reconciliation Bridge integration (Chunk 38).
  covering_directives: CoveringDirective[];
};

export type GenerateGapReportRequest = Record<string, never>;

// ---------------------------------------------------------------------------
// Chunk 37 amendments — Divergence Map (D284)
// ---------------------------------------------------------------------------

export type DivergenceMapEntry = {
  element_name: string;
  element_type: string;
  instance_count: number;
  source_origins?: Array<"document" | "communication">;
};

export type DivergenceMapBucketName =
  | "additive_A"
  | "additive_B"
  | "contradictory"
  | "consensus";

export type DivergenceMapBucket = {
  bucket_name: DivergenceMapBucketName;
  entries: DivergenceMapEntry[];
};

export type DivergenceMapResponse = {
  map_id: string;
  segment_id: string | null;
  reviewer_a: string;
  reviewer_b: string;
  version_a_id: string;
  version_b_id: string;
  buckets: DivergenceMapBucket[];
  generated_at: string;
  // D297 — Reconciliation Bridge integration (Chunk 38).
  covering_directives: CoveringDirective[];
};

export type DivergenceMapGenerateRequest = {
  version_a_id: string;
  version_b_id: string;
  segment_id?: string | null;
};

// ---------------------------------------------------------------------------
// Chunk 37 amendments — Documented Reality Report (D286 / D287)
// ---------------------------------------------------------------------------

export type DocumentedRealityAggregations = {
  top_entities: Record<string, unknown>[];
  top_relationships: Record<string, unknown>[];
  legal_entities: Record<string, unknown>[];
  monetary_flow: Record<string, unknown>;
  participants: Record<string, unknown>[];
  business_activity_signature: Record<string, unknown>;
  total_vertices: number;
  total_edges: number;
};

export type DocumentedRealityTrigger = "scheduled" | "on_demand";

export type DocumentedRealityReportResponse = {
  report_id: string;
  trigger: DocumentedRealityTrigger;
  corpus_below_floor: boolean;
  aggregations: DocumentedRealityAggregations;
  narrative: string | null;
  generated_at: string;
};

export type DocumentedRealityCadence =
  | "quarterly"
  | "monthly"
  | "on_demand";

export type DocumentedRealityScheduleResponse = {
  id: string;
  cadence: DocumentedRealityCadence;
  next_run_at: string | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type DocumentedRealityScheduleRequest = {
  cadence: DocumentedRealityCadence;
  enabled?: boolean;
};

export type DocumentedRealityScheduleUpdateRequest = {
  cadence?: DocumentedRealityCadence;
  enabled?: boolean;
};
