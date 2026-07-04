/**
 * Proposal API client (Chunk 47, CP7 / D389).
 *
 * Thin typed wrapper over the three routes mounted at
 * `/api/ontology/proposals/*` in `src/api/proposal_routes.py`.
 *
 * D120/D217: raw_confidence is present in the API response but the
 * frontend MUST NOT render it as a numeric value — use the band
 * helpers in `frontend/components/proposals/` instead.
 */

import { apiRequest } from "./client";

/** Mirrors ``src/ontology/evidence_bundle.py`` EvidenceBundle (Chunk 47 CP8). */
export type EvidenceBundle = {
  source_signal_ids: string[];
  signal_type: string;
  signal_strength: number;
  affected_entity_types: string[];
  ontology_module: string;
  example_documents: string[];
  example_text_snippets: string[];
  extraction_failure_count?: number | null;
  co_occurrence_count?: number | null;
  cq_text?: string | null;
  evidence_summary_nl?: string | null;
};

export type ProposalListResponse = {
  items: ProposalItem[];
  next_cursor: string | null;
};

export type ProposalItem = {
  id: string;
  created_at: string;
  proposal_type: string;
  change_tier: number;
  kgcl_command: string;
  proposed_diff: Record<string, unknown>;
  evidence: EvidenceBundle;
  signal_type: string;
  raw_confidence: number;
  priority: string;
  status: string;
  current_schema_version_id: string;
  ontology_module: string;
  dedup_hash: string;
  overflow: boolean;
  generated_at: string | null;
  reviewer?: string | null;
  human_decision?: string | null;
  modification_distance?: number | null;
};

export type DecideRequest = {
  decision: "approved" | "rejected" | "modified" | "deferred";
  reviewer: string;
  modified_diff?: Record<string, unknown>;
  notes?: string;
};

function appendQuery(
  base: string,
  params: Record<string, string | number | undefined | null>,
): string {
  const entries = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== "",
  );
  if (entries.length === 0) return base;
  const qs = entries
    .map(
      ([k, v]) =>
        `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`,
    )
    .join("&");
  return `${base}?${qs}`;
}

export const proposalsApi = {
  async list(opts?: {
    tier?: number;
    status?: string;
    ontology_module?: string;
    cursor?: string | null;
    limit?: number;
  }): Promise<ProposalListResponse> {
    const url = appendQuery("/api/ontology/proposals", {
      tier: opts?.tier,
      status: opts?.status,
      ontology_module: opts?.ontology_module,
      cursor: opts?.cursor,
      limit: opts?.limit,
    });
    return apiRequest<ProposalListResponse>(url);
  },

  async get(proposalId: string): Promise<ProposalItem> {
    return apiRequest<ProposalItem>(
      `/api/ontology/proposals/${encodeURIComponent(proposalId)}`,
    );
  },

  async decide(
    proposalId: string,
    body: DecideRequest,
  ): Promise<ProposalItem> {
    return apiRequest<ProposalItem>(
      `/api/ontology/proposals/${encodeURIComponent(proposalId)}/decide`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
  },
};
