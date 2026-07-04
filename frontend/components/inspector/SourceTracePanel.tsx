"use client";

// D217: resolve `RankedResult.grace_id` → full entity record via existing
// GET /api/graph/entities/{grace_id}. Render source_document_id +
// extraction_event_id + ontology_module + human_validated; no numeric
// `extraction_confidence` in DOM.

import { useEntity } from "@/lib/query/graph";
import type { RankedResult } from "@/lib/api/types";

export type SourceTracePanelProps = {
  result: RankedResult | null;
};

export function SourceTracePanel({ result }: SourceTracePanelProps) {
  const graceId = result?.grace_id ?? null;
  const query = useEntity(graceId);

  if (!result) {
    return (
      <div
        data-testid="source-trace-empty"
        className="text-xs text-slate-500 p-3 bg-white border rounded-md"
      >
        Select a result to see its provenance.
      </div>
    );
  }

  const entity = (query.data as Record<string, unknown> | undefined) ?? {};
  const source_document_id =
    typeof entity.source_document_id === "string"
      ? entity.source_document_id
      : null;
  const extraction_event_id =
    typeof entity.extraction_event_id === "string"
      ? entity.extraction_event_id
      : null;
  const ontology_module =
    typeof entity.ontology_module === "string" ? entity.ontology_module : null;
  const human_validated = Boolean(entity.human_validated);

  return (
    <div
      data-testid="source-trace-panel"
      className="bg-white border rounded-md p-3 space-y-1"
    >
      <h3 className="text-xs font-semibold text-slate-600 uppercase tracking-wide mb-1">
        Source trace — {result.name}
      </h3>
      <p
        className="text-xs text-slate-500 font-mono truncate"
        data-testid="source-trace-grace-id"
      >
        {result.grace_id}
      </p>
      {query.isLoading ? (
        <p className="text-xs text-slate-500">Resolving provenance…</p>
      ) : (
        <dl className="text-xs space-y-1">
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[140px]">Source document</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {source_document_id ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[140px]">Extraction event</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {extraction_event_id ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[140px]">Ontology module</dt>
            <dd className="text-slate-800 flex-1">
              {ontology_module ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[140px]">Human validated</dt>
            <dd
              className="text-slate-800 flex-1"
              data-testid="source-trace-human-validated"
            >
              {human_validated ? "✓ validated" : "— pending"}
            </dd>
          </div>
        </dl>
      )}
    </div>
  );
}
