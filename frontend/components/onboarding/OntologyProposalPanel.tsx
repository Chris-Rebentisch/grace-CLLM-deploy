"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { apiClient } from "@/lib/api/client";
import type { SchemaRunStatus, SeedSchemaData } from "@/lib/api/types";

type Phase = "idle" | "extracting" | "merging" | "ready" | "starting" | "error";

const TERMINAL_OK = "completed";

/**
 * Orchestrates the proposal->review bootstrap that was previously CLI-only:
 *   POST schema/extract -> poll extraction-status
 *   -> POST schema/merge -> poll merge-status -> GET seed-schema
 *   -> POST ontology/review/start -> navigate to /review?session_id=...
 *
 * This is the load-bearing gap (stage 7): the UI could never create a review
 * session, so /review always showed "Start a session to begin review."
 */
export function OntologyProposalPanel({
  docCount,
  cqReady,
}: {
  docCount: number;
  cqReady: boolean;
}) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const [proposal, setProposal] = useState<{
    mergeRunId: string;
    seed: SeedSchemaData;
    entityTypes: number;
    relationships: number;
  } | null>(null);

  // Poll a status endpoint until it reports completed (or fails / times out).
  // Schema extraction runs gpt-oss:120b, so the ceiling is generous.
  async function pollUntilDone(
    getStatus: () => Promise<SchemaRunStatus>,
    label: string,
    maxMs = 900_000,
  ): Promise<SchemaRunStatus> {
    const start = Date.now();
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const s = await getStatus();
      setMsg(`${label}: ${s.status}`);
      if (s.status === TERMINAL_OK) return s;
      if (s.status === "failed" || s.error) {
        throw new Error(s.error ?? `${label} failed`);
      }
      if (Date.now() - start > maxMs) throw new Error(`${label} timed out`);
      await new Promise((r) => setTimeout(r, 2500));
    }
  }

  async function generate() {
    setError("");
    setProposal(null);
    try {
      setPhase("extracting");
      setMsg("Starting schema extraction…");
      const ext = await apiClient.extractSchema({});
      const extDone = await pollUntilDone(
        () => apiClient.getExtractionStatus(ext.run_id),
        "Extracting types",
      );

      setPhase("merging");
      setMsg("Merging types into a proposed ontology…");
      const mrg = await apiClient.mergeSchema({ extraction_run_id: ext.run_id });
      const mrgDone = await pollUntilDone(
        () => apiClient.getMergeStatus(mrg.run_id),
        "Merging",
      );

      const seed = await apiClient.getSeedSchema(mrg.run_id);
      const entityTypes = Array.isArray(seed.entity_types)
        ? seed.entity_types.length
        : (mrgDone.merged_entity_types ?? extDone.total_entity_types ?? 0);
      const relationships = Array.isArray(seed.relationships)
        ? seed.relationships.length
        : (mrgDone.merged_relationships ?? extDone.total_relationships ?? 0);

      setProposal({ mergeRunId: mrg.run_id, seed, entityTypes, relationships });
      setPhase("ready");
      setMsg("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Proposal generation failed");
      setPhase("error");
    }
  }

  async function startReview() {
    if (!proposal) return;
    try {
      setPhase("starting");
      const session = await apiClient.startReview({
        merge_run_id: proposal.mergeRunId,
        reviewer: "operator",
        seed_schema_data: proposal.seed,
      });
      router.push(`/review?session_id=${encodeURIComponent(session.id)}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start review");
      setPhase("error");
    }
  }

  const busy = phase === "extracting" || phase === "merging" || phase === "starting";

  return (
    <div
      data-testid="ontology-proposal-panel"
      className="space-y-2 rounded border bg-white p-3 text-xs"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Propose ontology</h2>
        <button
          type="button"
          data-testid="generate-proposal"
          disabled={busy || docCount === 0 || !cqReady || phase === "ready"}
          onClick={() => void generate()}
          className="rounded bg-indigo-700 px-3 py-1 text-white disabled:opacity-50"
        >
          {phase === "extracting"
            ? "Extracting…"
            : phase === "merging"
              ? "Merging…"
              : phase === "ready"
                ? "Proposal ready"
                : `Generate proposal from ${docCount} docs`}
        </button>
      </div>

      <p className="text-slate-500">
        Runs CQ-driven schema extraction (gpt-oss:120b) over your processed
        documents, then merges the result into a proposed ontology. This can take
        a few minutes.
      </p>

      {!cqReady && (
        <p data-testid="proposal-needs-cqs" className="text-amber-700">
          Generate competency questions first — schema extraction is driven by them.
        </p>
      )}

      {busy && (
        <div data-testid="proposal-progress" className="space-y-1">
          <div className="h-2 w-full overflow-hidden rounded bg-slate-100">
            <div className="h-full w-2/3 animate-pulse bg-indigo-500" />
          </div>
          <p className="text-slate-600">{msg}</p>
        </div>
      )}

      {error && (
        <p data-testid="proposal-error" className="text-rose-600">
          {error}
        </p>
      )}

      {phase === "ready" && proposal && (
        <div data-testid="proposal-ready" className="space-y-2 rounded bg-indigo-50 p-2">
          <p className="text-slate-700">
            Proposed ontology: <strong>{proposal.entityTypes}</strong> entity types,{" "}
            <strong>{proposal.relationships}</strong> relationships.
          </p>
          <button
            type="button"
            data-testid="start-review"
            onClick={() => void startReview()}
            className="rounded bg-emerald-700 px-3 py-1 text-white"
          >
            Start review →
          </button>
        </div>
      )}
    </div>
  );
}
