"use client";

import { useEffect } from "react";
import type { RelationshipRecord } from "@/lib/api/types";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { sha256Hex } from "@/lib/ids/hash";

export type EdgeDetailPanelProps = {
  edge: RelationshipRecord | null;
  onClose?: () => void;
};

// D217: no numeric extraction_confidence / relationship_confidence /
// span_confidence / rrf_score / rerank_score in the DOM.
export function EdgeDetailPanel({ edge, onClose }: EdgeDetailPanelProps) {
  // CP8 D215 — fire `graph_edge_inspected`. Hash the EDGE's own grace_id,
  // NOT source or target entity ids.
  useEffect(() => {
    if (!edge) return;
    let cancelled = false;
    sha256Hex(edge.grace_id).then((hash) => {
      if (cancelled) return;
      emitTelemetry("graph_edge_inspected", {
        relationship_type: edge.relationship_type,
        grace_id_hash: hash,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [edge?.grace_id, edge?.relationship_type]);

  if (!edge) {
    return (
      <aside
        data-testid="edge-detail-panel-empty"
        className="hidden"
        aria-hidden
      />
    );
  }

  const properties = Object.entries(edge.properties ?? {}).filter(
    ([k]) => !/^(extraction|relationship|span)_confidence$|^(rrf|rerank)_score$/.test(k),
  );

  return (
    <aside
      data-testid="edge-detail-panel"
      className="w-96 flex-shrink-0 border-l bg-white p-4 space-y-3 overflow-y-auto"
    >
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            {edge.relationship_type}
          </h2>
          <p
            className="text-xs text-slate-500 font-mono truncate"
            data-testid="edge-grace-id"
          >
            {edge.grace_id}
          </p>
        </div>
        {onClose && (
          <button
            type="button"
            data-testid="edge-detail-close"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700"
            aria-label="Close detail panel"
          >
            ×
          </button>
        )}
      </header>

      <section>
        <h3 className="text-xs font-semibold text-slate-600 mb-1 uppercase tracking-wide">
          Endpoints
        </h3>
        <dl className="text-xs space-y-1" data-testid="edge-endpoints">
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[80px]">Source</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {edge.source_grace_id}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[80px]">Target</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {edge.target_grace_id}
            </dd>
          </div>
        </dl>
      </section>

      <dl className="text-xs space-y-1">
        {edge.ontology_module && (
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[120px]">Ontology module</dt>
            <dd className="text-slate-800">{edge.ontology_module}</dd>
          </div>
        )}
        <div className="flex items-baseline gap-2">
          <dt className="text-slate-500 min-w-[120px]">Human validated</dt>
          <dd
            className="text-slate-800"
            data-testid="edge-human-validated-badge"
          >
            {edge.human_validated ? "✓ validated" : "— pending"}
          </dd>
        </div>
      </dl>

      {properties.length > 0 && (
        <section>
          <h3 className="text-xs font-semibold text-slate-600 mb-1 uppercase tracking-wide">
            Properties
          </h3>
          <dl
            className="text-xs bg-slate-50 rounded-md p-2 space-y-1"
            data-testid="edge-properties"
          >
            {properties.map(([k, v]) => (
              <div key={k} className="flex items-baseline gap-2">
                <dt className="text-slate-500 min-w-[120px]">{k}</dt>
                <dd className="text-slate-800 break-words flex-1">
                  {typeof v === "string" || typeof v === "number"
                    ? String(v)
                    : JSON.stringify(v)}
                </dd>
              </div>
            ))}
          </dl>
        </section>
      )}

      <section>
        <h3 className="text-xs font-semibold text-slate-600 mb-1 uppercase tracking-wide">
          Evidence chain
        </h3>
        <dl className="text-xs space-y-1" data-testid="edge-provenance">
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[120px]">Source document</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {edge.source_document_id ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[120px]">Extraction event</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {edge.extraction_event_id ?? "—"}
            </dd>
          </div>
        </dl>
      </section>
    </aside>
  );
}
