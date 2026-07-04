"use client";

import { useGraphStore } from "@/lib/state/graph-store";
import { getColorForModule } from "@/lib/graph/node-shape-map";

export type TypeCount = { type: string; count: number; module: string | null };

export type TypeFilterLegendProps = {
  entityTypes: TypeCount[];
  relationshipTypes: TypeCount[];
};

export function TypeFilterLegend(props: TypeFilterLegendProps) {
  const visibleEntityTypes = useGraphStore((s) => s.visibleEntityTypes);
  const visibleRelTypes = useGraphStore((s) => s.visibleRelationshipTypes);
  const toggleEntity = useGraphStore((s) => s.toggleEntityType);
  const toggleRel = useGraphStore((s) => s.toggleRelationshipType);

  return (
    <aside
      data-testid="type-filter-legend"
      className="w-64 flex-shrink-0 border-l bg-white p-3 space-y-4 overflow-y-auto"
    >
      <section>
        <h3 className="text-xs font-semibold text-slate-600 mb-2 uppercase tracking-wide">
          Entity types
        </h3>
        <ul className="space-y-1" data-testid="entity-type-list">
          {props.entityTypes.map((t) => {
            const checked = visibleEntityTypes.has(t.type);
            return (
              <li key={t.type} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  data-testid={`entity-toggle-${t.type}`}
                  checked={checked}
                  onChange={() => toggleEntity(t.type)}
                  aria-label={`Toggle ${t.type}`}
                />
                <span
                  className="inline-block w-3 h-3 rounded-sm"
                  style={{ backgroundColor: getColorForModule(t.module) }}
                  aria-hidden
                />
                <span className="text-slate-700 truncate flex-1">{t.type}</span>
                <span className="text-xs text-slate-500" data-testid={`entity-count-${t.type}`}>
                  {t.count}
                </span>
              </li>
            );
          })}
        </ul>
      </section>

      <section>
        <h3 className="text-xs font-semibold text-slate-600 mb-2 uppercase tracking-wide">
          Relationship types
        </h3>
        <ul className="space-y-1" data-testid="relationship-type-list">
          {props.relationshipTypes.map((t) => {
            const checked = visibleRelTypes.has(t.type);
            return (
              <li key={t.type} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  data-testid={`rel-toggle-${t.type}`}
                  checked={checked}
                  onChange={() => toggleRel(t.type)}
                  aria-label={`Toggle ${t.type}`}
                />
                <span className="text-slate-700 truncate flex-1">{t.type}</span>
                <span className="text-xs text-slate-500" data-testid={`rel-count-${t.type}`}>
                  {t.count}
                </span>
              </li>
            );
          })}
        </ul>
      </section>
    </aside>
  );
}
