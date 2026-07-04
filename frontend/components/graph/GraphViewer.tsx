"use client";

import { useEffect, useMemo, useRef } from "react";
import { emitTelemetry } from "@/lib/telemetry/bus";
import {
  useEntitiesList,
  useEntity,
  useRelationshipsList,
} from "@/lib/query/graph";
import { useGraphStore } from "@/lib/state/graph-store";
import type {
  EntityRecord,
  RelationshipRecord,
} from "@/lib/api/types";
import { GraphCanvas, type GraphEdgeData, type GraphNodeData } from "./GraphCanvas";
import { GraphToolbar } from "./GraphToolbar";
import { TypeFilterLegend, type TypeCount } from "./TypeFilterLegend";
import { NodeDetailPanel } from "./NodeDetailPanel";
import { EdgeDetailPanel } from "./EdgeDetailPanel";
import { NeighborhoodExpander } from "./NeighborhoodExpander";
import { PaginationControls } from "./PaginationControls";
import { GraphEmptyState } from "./GraphEmptyState";
import { GraphErrorState } from "./GraphErrorState";

function aggregateTypeCounts(records: EntityRecord[]): TypeCount[] {
  const counts = new Map<string, TypeCount>();
  for (const r of records) {
    const existing = counts.get(r.entity_type);
    if (existing) {
      existing.count += 1;
    } else {
      counts.set(r.entity_type, {
        type: r.entity_type,
        count: 1,
        module: r.ontology_module,
      });
    }
  }
  return Array.from(counts.values()).sort((a, b) => b.count - a.count);
}

function aggregateRelationshipTypeCounts(
  records: RelationshipRecord[],
): TypeCount[] {
  const counts = new Map<string, TypeCount>();
  for (const r of records) {
    const existing = counts.get(r.relationship_type);
    if (existing) existing.count += 1;
    else
      counts.set(r.relationship_type, {
        type: r.relationship_type,
        count: 1,
        module: null,
      });
  }
  return Array.from(counts.values()).sort((a, b) => b.count - a.count);
}

function entitiesToNodeData(records: EntityRecord[]): GraphNodeData[] {
  return records.map((r) => ({
    id: r.grace_id,
    label:
      typeof r.properties?.name === "string"
        ? (r.properties.name as string)
        : r.grace_id.slice(0, 8),
    entityType: r.entity_type,
    ontologyModule: r.ontology_module,
  }));
}

function relationshipsToEdgeData(
  records: RelationshipRecord[],
): GraphEdgeData[] {
  return records.map((r) => ({
    id: r.grace_id,
    source: r.source_grace_id,
    target: r.target_grace_id,
    label: r.relationship_type,
  }));
}

export function GraphViewer() {
  const layout = useGraphStore((s) => s.activeLayout);
  const cursor = useGraphStore((s) => s.paginationCursor);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const selectedEdgeId = useGraphStore((s) => s.selectedEdgeId);
  const selectNode = useGraphStore((s) => s.selectNode);
  const selectEdge = useGraphStore((s) => s.selectEdge);
  const visibleEntityTypes = useGraphStore((s) => s.visibleEntityTypes);
  const visibleRelTypes = useGraphStore((s) => s.visibleRelationshipTypes);

  const entitiesQuery = useEntitiesList({}, cursor, 25);
  const relsQuery = useRelationshipsList({}, null, 100);
  const selectedEntityQuery = useEntity(selectedNodeId);

  const entities = entitiesQuery.data?.entities ?? [];
  const relationships = relsQuery.data?.relationships ?? [];

  // CP8 D215 — fire `graph_viewer_opened` once on mount. `entity_count_estimated`
  // populated from the first resolved listEntities page, or null beforehand.
  const openedFiredRef = useRef(false);
  useEffect(() => {
    if (openedFiredRef.current) return;
    if (entitiesQuery.isFetching) return;
    openedFiredRef.current = true;
    emitTelemetry("graph_viewer_opened", {
      scope: "all",
      entity_count_estimated: entitiesQuery.isSuccess
        ? entities.length +
          (entitiesQuery.data?.next_cursor ? 1 : 0)
        : null,
    });
  }, [entitiesQuery.isFetching, entitiesQuery.isSuccess, entitiesQuery.data?.next_cursor, entities.length]);

  const entityTypeCounts = useMemo(
    () => aggregateTypeCounts(entities),
    [entities],
  );
  const relationshipTypeCounts = useMemo(
    () => aggregateRelationshipTypeCounts(relationships),
    [relationships],
  );

  const filteredEntities = useMemo(() => {
    if (visibleEntityTypes.size === 0) return entities;
    return entities.filter((e) => visibleEntityTypes.has(e.entity_type));
  }, [entities, visibleEntityTypes]);

  const filteredRelationships = useMemo(() => {
    if (visibleRelTypes.size === 0) return relationships;
    return relationships.filter((r) => visibleRelTypes.has(r.relationship_type));
  }, [relationships, visibleRelTypes]);

  const nodes = useMemo(
    () => entitiesToNodeData(filteredEntities),
    [filteredEntities],
  );
  const edges = useMemo(
    () => relationshipsToEdgeData(filteredRelationships),
    [filteredRelationships],
  );

  const selectedEdge =
    selectedEdgeId != null
      ? relationships.find((r) => r.grace_id === selectedEdgeId) ?? null
      : null;

  const selectedEntityFromData = selectedNodeId
    ? entities.find((e) => e.grace_id === selectedNodeId) ?? null
    : null;

  // Compose a lightweight EntityRecord from the single-entity query if the
  // selected grace_id isn't in the current page.
  const selectedEntityFallback: EntityRecord | null = useMemo(() => {
    const raw = selectedEntityQuery.data;
    if (!raw || typeof raw !== "object") return null;
    const r = raw as Record<string, unknown>;
    return {
      grace_id: String(r.grace_id ?? selectedNodeId ?? ""),
      entity_type: String(r["@type"] ?? r.entity_type ?? "Unknown"),
      properties: (typeof r.properties === "object" && r.properties !== null
        ? (r.properties as Record<string, unknown>)
        : r) as Record<string, unknown>,
      source_document_id:
        typeof r.source_document_id === "string" ? r.source_document_id : null,
      extraction_event_id:
        typeof r.extraction_event_id === "string"
          ? r.extraction_event_id
          : null,
      ontology_module:
        typeof r.ontology_module === "string" ? r.ontology_module : null,
      human_validated: Boolean(r.human_validated),
      valid_from: null,
      valid_to: null,
      extraction_confidence: null,
    };
  }, [selectedEntityQuery.data, selectedNodeId]);

  const selectedEntity = selectedEntityFromData ?? selectedEntityFallback;

  const isError = entitiesQuery.isError || relsQuery.isError;
  const isLoading = entitiesQuery.isLoading && relsQuery.isLoading;

  return (
    <div
      data-testid="graph-viewer"
      className="flex flex-col h-full w-full bg-slate-50"
    >
      <GraphToolbar />
      <div className="flex flex-1 overflow-hidden">
        <main className="flex-1 flex flex-col">
          {isError ? (
            <GraphErrorState />
          ) : isLoading ? (
            <div
              data-testid="graph-loading"
              className="flex items-center justify-center h-full text-sm text-slate-500"
            >
              Loading graph…
            </div>
          ) : entities.length === 0 ? (
            <GraphEmptyState />
          ) : (
            <div className="flex-1 relative" data-testid="graph-canvas-wrap">
              <GraphCanvas
                nodes={nodes}
                edges={edges}
                layout={layout}
                highlightedNodeIds={
                  selectedNodeId ? [selectedNodeId] : []
                }
                onNodeClick={(id) => selectNode(id)}
                onEdgeClick={(id) => selectEdge(id)}
              />
            </div>
          )}
          <NeighborhoodExpander graceId={selectedNodeId} />
          <PaginationControls
            nextCursor={entitiesQuery.data?.next_cursor ?? null}
          />
        </main>

        <TypeFilterLegend
          entityTypes={entityTypeCounts}
          relationshipTypes={relationshipTypeCounts}
        />

        {selectedNodeId && (
          <NodeDetailPanel
            entity={selectedEntity}
            onClose={() => selectNode(null)}
          />
        )}
        {selectedEdgeId && !selectedNodeId && (
          <EdgeDetailPanel
            edge={selectedEdge}
            onClose={() => selectEdge(null)}
          />
        )}
      </div>
    </div>
  );
}
