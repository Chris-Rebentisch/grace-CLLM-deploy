"use client";

import { useEffect } from "react";
import { useNeighborhood } from "@/lib/query/graph";
import type { EntityRecord } from "@/lib/api/types";
import { emitTelemetry } from "@/lib/telemetry/bus";
import { sha256Hex } from "@/lib/ids/hash";

export type NodeDetailPanelProps = {
  entity: EntityRecord | null;
  onClose?: () => void;
};

// D217: NO numeric extraction_confidence in DOM. `human_validated` surfaces
// as a checkmark/icon, not a number.
export function NodeDetailPanel({ entity, onClose }: NodeDetailPanelProps) {
  const graceId = entity?.grace_id ?? null;
  const neighborhoodQuery = useNeighborhood(graceId, 1, {
    enabled: !!graceId,
  });

  // CP8 D215 — fire `graph_node_inspected` when the panel opens for a new
  // entity. `grace_id_hash` is hex-SHA256 of the entity's grace_id (NOT raw).
  useEffect(() => {
    if (!entity) return;
    let cancelled = false;
    sha256Hex(entity.grace_id).then((hash) => {
      if (cancelled) return;
      emitTelemetry("graph_node_inspected", {
        entity_type: entity.entity_type,
        grace_id_hash: hash,
      });
    });
    return () => {
      cancelled = true;
    };
  }, [entity?.grace_id, entity?.entity_type]);

  if (!entity) {
    return (
      <aside
        data-testid="node-detail-panel-empty"
        className="hidden"
        aria-hidden
      />
    );
  }

  const properties = Object.entries(entity.properties ?? {}).filter(
    // D217: don't render these as numerals even if present in properties.
    ([k]) => !/^(extraction|relationship|span)_confidence$|^(rrf|rerank)_score$/.test(k),
  );

  const neighbors = neighborhoodQuery.data?.neighbors ?? [];

  return (
    <aside
      data-testid="node-detail-panel"
      className="w-96 flex-shrink-0 border-l bg-white p-4 space-y-3 overflow-y-auto"
    >
      <header className="flex items-start justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold text-slate-900">
            {entity.entity_type}
          </h2>
          <p
            className="text-xs text-slate-500 font-mono truncate"
            data-testid="node-grace-id"
          >
            {entity.grace_id}
          </p>
        </div>
        {onClose && (
          <button
            type="button"
            data-testid="node-detail-close"
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700"
            aria-label="Close detail panel"
          >
            ×
          </button>
        )}
      </header>

      <dl className="text-xs space-y-1">
        {entity.ontology_module && (
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[120px]">Ontology module</dt>
            <dd className="text-slate-800">{entity.ontology_module}</dd>
          </div>
        )}
        <div className="flex items-baseline gap-2">
          <dt className="text-slate-500 min-w-[120px]">Human validated</dt>
          <dd
            className="text-slate-800"
            data-testid="human-validated-badge"
          >
            {entity.human_validated ? "✓ validated" : "— pending"}
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
            data-testid="node-properties"
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
          Provenance
        </h3>
        <dl className="text-xs space-y-1" data-testid="node-provenance">
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[120px]">Source document</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {entity.source_document_id ?? "—"}
            </dd>
          </div>
          <div className="flex items-baseline gap-2">
            <dt className="text-slate-500 min-w-[120px]">Extraction event</dt>
            <dd className="text-slate-800 font-mono truncate flex-1">
              {entity.extraction_event_id ?? "—"}
            </dd>
          </div>
        </dl>
      </section>

      <section>
        <h3 className="text-xs font-semibold text-slate-600 mb-1 uppercase tracking-wide">
          Neighbors
        </h3>
        {neighborhoodQuery.isLoading ? (
          <p className="text-xs text-slate-500">Loading neighbors…</p>
        ) : neighbors.length === 0 ? (
          <p className="text-xs text-slate-500" data-testid="no-neighbors">
            No connected entities.
          </p>
        ) : (
          <ul
            className="text-xs space-y-1 max-h-40 overflow-y-auto"
            data-testid="neighbor-summary"
          >
            {neighbors.slice(0, 10).map((n, i) => {
              const rec = n as Record<string, unknown>;
              const id = String(rec.grace_id ?? `n-${i}`);
              const type = String(rec["@type"] ?? rec.entity_type ?? "Entity");
              const name = String(rec.name ?? id);
              return (
                <li key={id} className="truncate">
                  <span className="text-slate-600">{type}</span>
                  <span className="text-slate-900"> — {name}</span>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </aside>
  );
}
